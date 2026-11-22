import { Bell, BellRing } from 'lucide-react'
import { useEffect, useState } from 'react'
import { enableAlerts, pushSupported, registerServiceWorker, sendTestAlert } from '@/lib/push'

// Launch alerts on this device (web push). Independent of the cards on screen, so a fresh
// visit with an empty inbox can still turn them on.
export function AlertsButton() {
  const [on, setOn] = useState(false)
  const [info, setInfo] = useState('')
  const supported = pushSupported()

  useEffect(() => {
    void registerServiceWorker().then(async (reg) => {
      const sub = await reg?.pushManager.getSubscription()
      setOn(!!sub && Notification.permission === 'granted')
    })
  }, [])

  const say = (text: string) => {
    setInfo(text)
    window.setTimeout(() => setInfo(''), 6000)
  }

  return (
    <div className="relative">
      {info && (
        <div className="absolute right-0 top-11 z-50 w-72 rounded-xl bg-white px-4 py-3 text-[13.5px] text-ink-2 shadow-[0_8px_30px_-12px_rgba(0,0,0,.25)]">{info}</div>
      )}
      <button
        className="inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-full text-ink-3 hover:bg-soft hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
        onClick={async () => {
          if (!supported) return say('This browser cannot receive alerts. On iPhone: Share → Add to Home Screen, then open the app.')
          if (!on) {
            const text = await enableAlerts()
            setOn(text.startsWith('Alerts are on'))
            return say(text)
          }
          say(await sendTestAlert(''))
        }}
        title={on ? 'Send a test alert to this device' : 'Get launch alerts on this device'}
        aria-label={on ? 'Test alert' : 'Enable alerts'}
      >
        {on ? <BellRing size={17} /> : <Bell size={17} />}
      </button>
    </div>
  )
}
