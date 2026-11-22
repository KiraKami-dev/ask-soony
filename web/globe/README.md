# Parked: globe dashboard (Jonas / earlier Akshat)

The 3D globe UI from `jonas/alert-agent`, kept for reference. The live UI is `web/src` (decision inbox
and 2D supply map; see [docs/plan.md §5](../../docs/plan.md)). `VoiceCall.tsx` and `push.ts` talk to the voice and push routers that
the platform API still serves (`/api/voice/*`, `/api/push/*`); wire them into `DecisionCard` to bring
voice and phone alerts back into the inbox. `package.json.ref` lists the extra npm packages they need.
