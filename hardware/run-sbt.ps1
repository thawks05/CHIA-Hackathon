param([Parameter(ValueFromRemainingArguments = $true)][string[]]$SbtArguments = @('compile'))
$ErrorActionPreference = 'Stop'
$cacheRoot = Join-Path $PSScriptRoot '.tools'
New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null
$launcherPath = Join-Path $cacheRoot 'sbt-launch.jar'
if (-not (Test-Path -LiteralPath $launcherPath)) {
    Invoke-WebRequest -Uri 'https://repo.maven.apache.org/maven2/org/scala-sbt/sbt-launch/1.9.7/sbt-launch-1.9.7.jar' -OutFile $launcherPath
}
$repositoryPath = Join-Path $cacheRoot 'repositories'
[IO.File]::WriteAllText($repositoryPath, "[repositories]`nlocal`nmaven-central: https://repo.maven.apache.org/maven2`n")
$jvmArguments = @(
    "-Dsbt.boot.directory=$cacheRoot/boot",
    "-Dsbt.global.base=$cacheRoot/global",
    "-Dsbt.ivy.home=$cacheRoot/ivy",
    "-Dsbt.coursier.home=$cacheRoot/coursier",
    '-Dsbt.override.build.repos=true',
    "-Dsbt.repository.config=$repositoryPath",
    '-Dsbt.log.noformat=true'
)
$previousCoursierCache = $env:COURSIER_CACHE
$runExitCode = 1
Push-Location $PSScriptRoot
try {
    $env:COURSIER_CACHE = Join-Path $cacheRoot 'coursier'
    & java @jvmArguments -jar $launcherPath @SbtArguments
    $runExitCode = $LASTEXITCODE
} finally {
    $env:COURSIER_CACHE = $previousCoursierCache
    Pop-Location
}
exit $runExitCode
