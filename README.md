# MediKeep Epic sign-in relay (sandbox prototype)

`epic/callback/` is the fixed redirect URI of the sandbox Epic app "MediKeep" (public client,
dynamic client registration). Each install puts its callback URL in `state` as
`<base64url(https://host/api/v1/health-connections/callback)>.<random>`; the page validates it and
forwards `code`/`state`, asking the user to confirm a server it has not seen before.
Design: dan/truenas-automation docs/superpowers/specs/2026-11-03-medikeep-production-sync-design.md §5.7a.
Intended to move to a MediKeep-maintainer-owned host if upstream adopts it.
