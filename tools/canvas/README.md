# Local canvas module

The canvas is being migrated away from Excalidraw in small, compatible steps.
The first step is complete: image elements, numbered labels, and simple
rectangle/text elements created by Dewiar now come from
`local-elements.js`, without calling Excalidraw's element converter. The
existing scene shape is retained temporarily so old documents continue to
open while the renderer is replaced.

Build from this directory: `npm ci --ignore-scripts && npm run build`.
Node is a build dependency only. Installed Dewiar uses the generated files in
`ui/assets/vendor/canvas/`. Versions and dependency integrity are in package-lock.json;
output hashes are in vendor/canvas/manifest.json.

- Excalidraw 0.18.1, React/ReactDOM 18.3.1.
- mermaid-to-excalidraw 2.2.2, matching Excalidraw's own dependency.
- esbuild 0.25.12, production ESM with local chunks and CSS.
- All upstream fonts are shipped locally (including CJK). The complete bundle is
  about 22 MiB; this preserves upstream font support and avoids silent CDN loads.
- One explicit, version-checked build patch removes Excalidraw's unconditional
  font CDN fallback. Updating the package must review this patch; the build fails
  if the pinned source changes. No runtime private editor API is used.
- licenses/ contains upstream MIT and notices extracted from the distributed font
  metadata. build.mjs also assembles license/notice texts for bundled npm packages.
- Images use Dewiar's original-preserving importer; the stock image import tool is
  disabled. Clipboard and file drops are intercepted only outside text inputs.
- Network CSP allows local HTTP/WebSocket, data and blob URLs. No remote service,
  collaboration server, AI provider or image generation is configured. Native
  WebKit's JS bridge requires unsafe-eval; external script sources remain blocked.

References: https://docs.excalidraw.com/docs/@excalidraw/excalidraw/integration
and https://docs.excalidraw.com/docs/@excalidraw/mermaid-to-excalidraw/api .

Acceptance: tests/canvas_test.py (Chromium + WebKit, real bridge and >16 MiB PNG),
tests/canvas_native_test.py (Cocoa + 6K export + system close).
