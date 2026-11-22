import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { CountPage } from './components/CountPage.tsx'
import { OkPage } from './components/OkPage.tsx'

// /count/<token>: the site's count page, outside the dashboard (no state, no password).
const count = window.location.pathname.match(/^\/count\/([\w-]+)\/?$/)
// /ok/<token>: a passed-on decision or an internal message for one person, same rule.
const ok = window.location.pathname.match(/^\/ok\/([\w-]+)\/?$/)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {count ? <CountPage token={count[1]} /> : ok ? <OkPage token={ok[1]} /> : <App />}
  </StrictMode>,
)
