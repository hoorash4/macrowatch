const fs = require('fs');
const { execSync } = require('child_process');
const testFiles = fs.readdirSync(__dirname).filter(f => f.startsWith('test_') && f.endsWith('.js'));
const nodePath = process.execPath;

let totalPassed = 0;
let totalFailed = 0;
const failures = [];

for (const f of testFiles) {
  try {
    execSync(`"${nodePath}" "${__dirname}/${f}"`, { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] });
    totalPassed++;
  } catch (err) {
    totalFailed++;
    failures.push({ file: f, output: (err.stdout || '') + (err.stderr || '') });
  }
}

if (totalFailed > 0) {
  console.error(`Failed ${totalFailed} of ${testFiles.length}:`);
  failures.forEach(fail => {
    console.error(`--- ${fail.file} ---`);
    console.error(fail.output.slice(0, 500));
  });
  process.exit(1);
} else {
  console.log(`All ${totalPassed} test suites passed!`);
  process.exit(0);
}
