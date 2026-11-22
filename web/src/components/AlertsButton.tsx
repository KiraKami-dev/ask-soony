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
        <div className="absolute right-0 top-11 z-50 w-72 rounded-xl border border-line bg-white px-4 py-2 text-sm text-ink-2 shadow-lg">{info}</div>
      )}
      <button
        className="inline-flex h-9 cursor-pointer items-center gap-2 whitespace-nowrap rounded-full border border-line bg-white px-2.5 text-sm 2xl:px-3.5 font-semibold text-ink-2 hover:bg-panel"
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
        {on ? <BellRing size={16} className="text-ok" /> : <Bell size={16} />}
        <span className="hidden 2xl:inline">{on ? 'Test alert' : 'Enable alerts'}</span>
      </button>
    </div>
  )
}
