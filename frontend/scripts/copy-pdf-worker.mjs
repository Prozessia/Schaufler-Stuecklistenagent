// Keep public/pdf.worker.min.mjs in lockstep with the pdfjs that react-pdf imports.
//
// react-pdf re-exports its OWN (possibly nested) copy of pdfjs-dist, whose version
// can differ from the top-level dependency (currently react-pdf -> 5.4.296 while the
// top-level pdfjs-dist is 5.6.205). pdfjs throws at runtime if the worker version does
// not match `pdfjs.version` exactly. Resolving the worker relative to react-pdf — and
// running this on predev/prebuild — guarantees they always match, instead of a hand-
// copied file that silently drifts on the next `npm install`.

import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const scriptDir = dirname(fileURLToPath(import.meta.url));
const publicDir = join(scriptDir, "..", "public");
const dest = join(publicDir, "pdf.worker.min.mjs");

const reactPdfDir = dirname(require.resolve("react-pdf/package.json"));
const pdfjsPkgPath = require.resolve("pdfjs-dist/package.json", {
  paths: [reactPdfDir],
});
const pdfjsDir = dirname(pdfjsPkgPath);
const version = require(pdfjsPkgPath).version;

const candidates = ["build/pdf.worker.min.mjs", "build/pdf.worker.mjs"];
const src = candidates.map((rel) => join(pdfjsDir, rel)).find(existsSync);

if (!src) {
  console.error(
    `[pdf-worker] No worker file found for pdfjs ${version} under ${pdfjsDir}`
  );
  process.exit(1);
}

mkdirSync(publicDir, { recursive: true });
copyFileSync(src, dest);
console.log(
  `[pdf-worker] pdfjs ${version}: copied ${src} -> public/pdf.worker.min.mjs`
);
