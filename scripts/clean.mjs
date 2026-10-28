/** Only delete generated files. Never touch .venv or application data. */

import fs from 'node:fs'
import path from 'node:path'

for (const directory of ['out', 'build', 'dist']) {
  fs.rmSync(path.resolve(directory), { recursive: true, force: true })
  console.log('Removed', directory)
}
