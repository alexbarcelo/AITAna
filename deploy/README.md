# Deploying AITAna on Kubernetes

These are plain Kustomize manifests with no Helm. `base/` holds everything the app
ships itself. `overlays/example/` is a starting point: copy it once per
environment (say `overlays/prod/`) and adjust it there.

```bash
kubectl kustomize deploy/overlays/example        # render
kubectl apply -k deploy/overlays/example         # apply
```

## What's in the base

| Resource           | Image                                 | Notes                                   |
|--------------------|---------------------------------------|-----------------------------------------|
| `aitana-api`       | `ghcr.io/alexbarcelo/aitana-api`      | FastAPI, Service on 8080                |
| `aitana-worker`    | `ghcr.io/alexbarcelo/aitana-api`      | Celery, same image, different command   |
| `aitana-frontend`  | `ghcr.io/alexbarcelo/aitana-frontend` | nginx: SPA + `/api/` proxy, Service 8080 |
| `aitana-redis`     | `redis:7-alpine`                      | Celery broker only, no persistence      |
| `aitana-gotenberg` | `gotenberg/gotenberg:8`               | `.docx` to PDF, stateless               |

Resource names are fixed; don't add a `namePrefix`. The frontend's nginx
(`API_UPSTREAM`) and the `REDIS_URL`/`GOTENBERG_URL` config values refer to
these names. For a second instance, use a second namespace.

The browser only ever talks to the frontend. nginx serves the built SPA and
forwards `/api/*` to `aitana-api` with the `/api` prefix stripped, so the
Ingress needs just one host rule and no controller-specific rewrites.

## What DevOps provides

The manifests reference these but don't create them:

- **PostgreSQL** (14 or newer), reachable through `DATABASE_URL` (any
  `postgres://`/`postgresql://` URI). An empty database owned by the app's
  user is enough: the api's `migrate` initContainer creates and upgrades the
  schema (`alembic upgrade head`) before every start. The example overlay
  can run one for you instead, see "PostgreSQL with CloudNativePG" below.
- **S3-compatible object storage** (MinIO, Ceph RGW, AWS S3, ...). Set
  `MINIO_ENDPOINT` (host[:port], no scheme), `MINIO_SECURE` and `MINIO_BUCKET`
  in the overlay's `config.env`. At startup the API checks that the bucket
  exists and creates it if it doesn't, so the credentials need
  `s3:ListBucket` on it (plus `CreateBucket` unless the bucket is pre-created)
  and object read/write/delete.
- **The `aitana-secrets` Secret**, in the same namespace. The api and worker
  load it through `envFrom`. Keys (see `overlays/example/secret.env.example`):
  - `DATABASE_URL` (unless the CloudNativePG example provides it)
  - `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`
  - whichever LLM providers are used: `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
    `OLLAMA_BASE_URL`, `ANTHROPIC_API_KEY` (the stock image doesn't install the
    Anthropic provider, see the root `Dockerfile`)
- **An Ingress controller and TLS.** The example `ingress.yaml` is written
  for ingress-nginx. Its default 1 MB body limit would break uploads, so the
  example raises it to 200m, matching the frontend's `MAX_BODY_SIZE`.

Non-secret settings live in the generated `aitana-config` ConfigMap (see
`src/aitana/settings.py` for every option). Its name carries a content hash, so
editing `config.env` rolls the api and worker pods.

## Egress (if NetworkPolicies apply)

- api and worker: PostgreSQL, the S3 endpoint, `aitana-redis`, the LLM provider
  APIs (or the Ollama host).
- api: `aitana-gotenberg`.
- worker (and api, for the "test this question" endpoint): `cdn.jsdelivr.net`
  for the Python grading sandbox (Pyodide fetches its packages from there).
- frontend: `aitana-api`.

## Operational notes

- **Images and tags.** The base tracks `:main`. Pin a release (`v1.2.3` git
  tag, published as image tag `1.2.3`) or an immutable `sha-xxxxxxx` in the
  overlay's `images:` block. Both images are multi-arch (amd64 + arm64).
- **Redis has no persistence.** A restart drops queued grading tasks, and those
  submissions stay `pending` until someone regrades them from the UI.
- **Worker rollouts** get a 120s grace period so an in-flight submission can
  finish. A submission interrupted anyway shows as stuck in progress; regrade
  it.
- **Scaling.** The api's startup steps (migrations, model seeding, bucket check)
  are idempotent, and concurrent migrations wait on a PostgreSQL advisory
  lock, so more api replicas are safe. Scale the worker through `replicas`, or
  the `--concurrency` flag in `base/worker.yaml`.
- **Database connections.** Each api pod keeps a small connection pool; each
  worker task opens its own connection for the duration of the task (no
  pool), so expect roughly api pods x 5-15 plus worker concurrency in use.
- **Everything runs as non-root** (backend uid 10001, nginx 101, redis 999,
  gotenberg 1001). The root filesystem is *not* read-only: the grading
  sandbox writes its Deno/Pyodide cache under `/app`.

## PostgreSQL with CloudNativePG

`overlays/example/` includes an in-cluster PostgreSQL run by the
[CloudNativePG](https://cloudnative-pg.io) operator, as an example of one way
to get a database. To use an existing PostgreSQL instead, remove
`postgres-cluster.yaml` and the `database-url.yaml` patch from the overlay's
`kustomization.yaml` and put `DATABASE_URL` in `aitana-secrets`.

1. **Install the operator** once per cluster (cluster-admin), with the release
   manifest or the `cnpg/cloudnative-pg` Helm chart -- see the CloudNativePG
   installation docs for the current version:

   ```bash
   kubectl apply --server-side -f \
     https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.27/releases/cnpg-1.27.0.yaml
   kubectl -n cnpg-system rollout status deployment cnpg-controller-manager
   ```

2. **Apply the overlay.** `postgres-cluster.yaml` declares a `Cluster` named
   `aitana-db` that bootstraps an `aitana` database owned by an `aitana` user.
   The operator then creates the `aitana-db-rw` Service and the `aitana-db-app`
   Secret, and `database-url.yaml` passes that Secret's `uri` key to the api
   (and its `migrate` initContainer) and the worker as `DATABASE_URL`. Until
   the database is ready the api pod waits in its initContainer, which is
   expected.

   ```bash
   kubectl apply -k deploy/overlays/example
   kubectl -n aitana get cluster aitana-db      # wait for "Cluster in healthy state"
   kubectl -n aitana logs deploy/aitana-api -c migrate
   ```

3. **Size it for real use.** `instances: 3` gives a primary plus two
   replicas with automatic failover (the app always connects through
   `aitana-db-rw`, which follows the primary). Set `storage.size` and
   `storageClass` for your cluster.

4. **Backups.** Enable the commented Barman Cloud plugin section in
   `postgres-cluster.yaml` (WAL archiving plus a daily base backup to any S3
   endpoint) after installing the
   [plugin](https://cloudnative-pg.io/plugin-barman-cloud/). Without it, the
   database lives only on the cluster's volumes.

5. **Connecting by hand**, e.g. for inspection:

   ```bash
   kubectl -n aitana exec -it aitana-db-1 -- psql aitana
   ```

The operator also handles minor-version upgrades (change `imageName`), and
connection pooling is available through its `Pooler` resource; this app's
connection count doesn't need one (if you add it, use session pooling).
