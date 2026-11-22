import { Bell, BellRing } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { disableAlerts, enableAlerts, pushSupported, registerServiceWorker, sendTestAlert } from '@/lib/push'

// Launch alerts on this device (web push). Independent of the cards on screen, so a fresh
// visit with an empty inbox can still turn them on. Off: one tap turns them on. On: a small
// menu says so, sends a test alert or turns them off on this device.
export function AlertsButton() {
  const [on, setOn] = useState(false)
  const [open, setOpen] = useState(false)
  const [info, setInfo] = useState('')
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const supported = pushSupported()

  useEffect(() => {
    void registerServiceWorker().then(async (reg) => {
      const sub = await reg?.pushManager.getSubscription()
      setOn(!!sub && Notification.permission === 'granted')
    })
  }, [])
  useEffect(() => {
    if (!open) return
    const off = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', off)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', off); document.removeEventListener('keydown', esc) }
  }, [open])

  const say = (text: string) => {
    setInfo(text)
    window.setTimeout(() => setInfo(''), 6000)
  }
  const run = async (f: () => Promise<void>) => { setBusy(true); try { await f() } finally { setBusy(false) } }
  const item = 'block w-full cursor-pointer rounded-lg px-3 py-2 text-left text-[14px] text-ink-2 hover:bg-soft hover:text-ink disabled:opacity-50'

  return (
    <div ref={ref} className="relative">
      {info && !open && (
        <div className="absolute right-0 top-11 z-50 w-72 rounded-xl bg-white px-4 py-3 text-[13.5px] text-ink-2 shadow-[0_8px_30px_-12px_rgba(0,0,0,.25)]" role="status">{info}</div>
      )}
      {open && (
        <div className="absolute right-0 top-11 z-50 w-64 rounded-2xl bg-white p-1.5 shadow-[0_12px_40px_-12px_rgba(0,0,0,.28)]" role="menu">
          <p className="px-3 pb-1 pt-2 text-[13px] text-ink-3">Alerts are on for this device</p>
          <button role="menuitem" disabled={busy} className={item} onClick={() => run(async () => { setOpen(false); say(await sendTestAlert('')) })}>
            Send a test alert
          </button>
          <button role="menuitem" disabled={busy} className={item} onClick={() => run(async () => { setOpen(false); say(await disableAlerts()); setOn(false) })}>
            Turn off on this device
          </button>
        </div>
      )}
      <button
        className="inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-full text-ink-3 hover:bg-soft hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
        onClick={async () => {
          if (!supported) return say('This browser cannot receive alerts. On iPhone: Share → Add to Home Screen, then open the app.')
          if (on) return setOpen(!open)
          const text = await enableAlerts()
          setOn(text.startsWith('Alerts are on'))
          say(text)
        }}
        title={on ? 'Alerts are on' : 'Get launch alerts on this device'}
        aria-label={on ? 'Alerts are on: options' : 'Enable alerts'}
        aria-expanded={on ? open : undefined}
      >
        {on ? <BellRing size={17} /> : <Bell size={17} />}
      </button>
    </div>
  )
}
