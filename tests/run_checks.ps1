$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$bundledRuntime = 'C:\Users\hoora\.cache\codex-runtimes\codex-primary-runtime\dependencies'
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$node = if ($nodeCommand) { $nodeCommand.Source } else { Join-Path $bundledRuntime 'node\bin\node.exe' }
$bundledPython = Join-Path $bundledRuntime 'python\python.exe'
# WindowsApps의 python.exe는 설치 안내용 별칭이라 실제 테스트를 실행하지 않는다.
$python = if ($pythonCommand -and $pythonCommand.Source -notlike '*\WindowsApps\*') {
  $pythonCommand.Source
} else {
  $bundledPython
}

Push-Location $root
try {
  foreach ($file in @('script.js', 'admin.js', 'auth.js')) {
    & $node --check $file
  }
  # --test는 일부 제한 환경에서 자식 프로세스를 만들기 때문에 파일을 직접 실행한다.
  & $node tests/test_dashboard.js
  & $python -m unittest discover -s tests -p 'test_*.py' -v
} finally {
  Pop-Location
}
