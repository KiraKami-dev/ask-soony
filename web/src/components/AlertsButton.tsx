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
    <div className="fixed bottom-4 right-4 z-40 flex flex-col items-end gap-2">
      {info && (
        <div className="max-w-xs rounded-xl border border-line bg-white px-4 py-2 text-sm text-ink-2 shadow-lg">{info}</div>
      )}
      <button
        className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-line bg-white px-4 py-2 text-sm font-semibold text-ink-2 shadow-md hover:bg-panel"
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
      >
        {on ? <BellRing size={16} className="text-ok" /> : <Bell size={16} />}
        {on ? 'Test alert' : 'Enable alerts'}
      </button>
    </div>
  )
}
