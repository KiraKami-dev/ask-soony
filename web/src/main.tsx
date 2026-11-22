import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { CountPage } from './components/CountPage.tsx'

// /count/<token>: the site's count page, outside the dashboard (no state, no password).
const count = window.location.pathname.match(/^\/count\/([\w-]+)\/?$/)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {count ? <CountPage token={count[1]} /> : <App />}
  </StrictMode>,
)
