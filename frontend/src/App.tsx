import { NavLink, Route, Routes } from 'react-router-dom'
import BatchDetailPage from './pages/BatchDetailPage'
import BatchesPage from './pages/BatchesPage'
import CoursesPage from './pages/CoursesPage'
import EditionsPage from './pages/EditionsPage'
import RubricDetailPage from './pages/RubricDetailPage'
import RubricsPage from './pages/RubricsPage'
import StudentsPage from './pages/StudentsPage'
import SubmissionDetailPage from './pages/SubmissionDetailPage'
import SubmissionsPage from './pages/SubmissionsPage'
import UploadSubmissionPage from './pages/UploadSubmissionPage'

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-2 rounded-md text-sm font-medium ${
    isActive ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'
  }`

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center gap-2 px-4 py-3">
          <span className="mr-4 text-lg font-semibold">AITAna</span>
          <nav className="flex gap-1">
            <NavLink to="/" end className={navLinkClass}>
              Submissions
            </NavLink>
            <NavLink to="/students" className={navLinkClass}>
              Students
            </NavLink>
            <NavLink to="/courses" className={navLinkClass}>
              Courses
            </NavLink>
            <NavLink to="/editions" className={navLinkClass}>
              Editions
            </NavLink>
            <NavLink to="/rubrics" className={navLinkClass}>
              Rubrics
            </NavLink>
            <NavLink to="/upload" className={navLinkClass}>
              Upload submission
            </NavLink>
            <NavLink to="/batches" className={navLinkClass}>
              Batches
            </NavLink>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-4 py-8">
        <Routes>
          <Route path="/" element={<SubmissionsPage />} />
          <Route path="/students" element={<StudentsPage />} />
          <Route path="/courses" element={<CoursesPage />} />
          <Route path="/editions" element={<EditionsPage />} />
          <Route path="/rubrics" element={<RubricsPage />} />
          <Route path="/rubrics/:id" element={<RubricDetailPage />} />
          <Route path="/upload" element={<UploadSubmissionPage />} />
          <Route path="/batches" element={<BatchesPage />} />
          <Route path="/batches/:id" element={<BatchDetailPage />} />
          <Route path="/submissions/:id" element={<SubmissionDetailPage />} />
        </Routes>
      </main>
    </div>
  )
}
