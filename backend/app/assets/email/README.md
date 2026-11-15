`trace-logo.png` is an email-compatible, 3x raster export of
`frontend/public/brand/trace-logo.svg`, on white for consistent email rendering.
It uses the SVG's Arial Black font fallback. Keep this file with the API when
deploying; no frontend build or externally hosted image is required at runtime.

The mail service embeds it with Content-ID `trace-logo` and displays it at
198 x 60 pixels in the common header for verification and account decision mail.
