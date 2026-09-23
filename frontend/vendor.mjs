// Copies the browser libraries into the package, so installing adminsite
// needs no Node and no CDN at runtime.
import { copyFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const target = resolve(here, "../src/adminsite/static");

const geist = "@fontsource-variable/geist";

const files = [
  ["htmx.org/dist/htmx.min.js", "htmx.min.js"],
  ["alpinejs/dist/cdn.min.js", "alpine.min.js"],
  // Geist, the typeface, in the two subsets European languages need. Any
  // other script falls back to the system's own font.
  [`${geist}/files/geist-latin-wght-normal.woff2`, "fonts/geist-latin.woff2"],
  [`${geist}/files/geist-latin-ext-wght-normal.woff2`, "fonts/geist-latin-ext.woff2"],
  [`${geist}/LICENSE`, "fonts/GEIST-LICENSE.txt"],
];

await mkdir(resolve(target, "fonts"), { recursive: true });

for (const [from, to] of files) {
  const source = resolve(here, "node_modules", from);
  await copyFile(source, resolve(target, to));
  console.log(`${to} copied`);
}
