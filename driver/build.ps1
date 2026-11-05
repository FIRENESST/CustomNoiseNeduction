<#
.SYNOPSIS
    Builds the NoiseMic virtual microphone driver package into driver\package.

.DESCRIPTION
    Restores the WDK from NuGet if needed, compiles NoiseMic.sys, writes the
    stamped UTF-16 NoiseMic.inf, checks it with InfVerif and creates
    NoiseMic.cat. Signing is optional: Windows only loads the driver when the
    package is Microsoft-signed (attestation) or, on a test machine with test
    signing enabled, signed with a certificate that machine trusts.

.PARAMETER Configuration
    Release (default) or Debug.

.PARAMETER Version
    DriverVer version written into the INF.

.PARAMETER CertificateThumbprint
    Signs NoiseMic.sys, NoiseMic.cat and NoiseMic.cab with this certificate
    from Cert:\CurrentUser\My: your EV code-signing certificate, or a test
    certificate.

.PARAMETER NewTestCertificate
    Creates a self-signed "NoiseMic Test" code-signing certificate in
    Cert:\CurrentUser\My, signs with it and exports NoiseMicTest.cer next to
    the package for import on the test machine.

.PARAMETER TimestampUrl
    RFC 3161 timestamp server for release signing. Ignored for test signing.

.PARAMETER Cab
    Also writes build\NoiseMic.cab, the submission file for Partner Center
    attestation signing.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -NewTestCertificate
    .\build.ps1 -CertificateThumbprint 0123...ABCD -Cab
#>
[CmdletBinding(DefaultParameterSetName = 'Unsigned')]
param(
    [ValidateSet('Release', 'Debug')]
    [string] $Configuration = 'Release',

    [ValidatePattern('^\d+\.\d+\.\d+\.\d+$')]
    [string] $Version = '1.0.0.0',

    [Parameter(ParameterSetName = 'Signed', Mandatory)]
    [string] $CertificateThumbprint,

    [Parameter(ParameterSetName = 'TestSigned', Mandatory)]
    [switch] $NewTestCertificate,

    [string] $TimestampUrl = 'http://timestamp.digicert.com',

    [switch] $Cab
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$WdkVersion = '10.0.28000.2526'
$KmdfVersion = '1.31'
$TargetOs = '10_VB_X64,10_CO_X64,10_NI_X64,10_GE_X64'

$root = $PSScriptRoot
$packages = Join-Path $root 'packages'
$buildDir = Join-Path $root 'build'
$packageDir = Join-Path $root 'package'
$sdkBin = Join-Path $packages "Microsoft.Windows.SDK.CPP.$WdkVersion\c\bin\10.0.28000.0\x64"
$wdkBin = Join-Path $packages "Microsoft.Windows.WDK.x64.$WdkVersion\c\bin\10.0.28000.0"
$wdkTools = Join-Path $packages "Microsoft.Windows.WDK.x64.$WdkVersion\c\tools\10.0.28000.0\x64"

function Invoke-Tool([string] $Path, [string[]] $Arguments) {
    & $Path @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$(Split-Path $Path -Leaf) failed with exit code $LASTEXITCODE." }
}

# --- WDK from NuGet ------------------------------------------------------------
foreach ($id in 'Microsoft.Windows.WDK.x64', 'Microsoft.Windows.SDK.CPP', 'Microsoft.Windows.SDK.CPP.x64') {
    $dest = Join-Path $packages "$id.$WdkVersion"
    if (Test-Path $dest) { continue }
    $download = Join-Path $packages '_downloads'
    New-Item -ItemType Directory -Force $download | Out-Null
    $zip = Join-Path $download "$id.$WdkVersion.zip"
    $lower = $id.ToLowerInvariant()
    Write-Host "Downloading $id $WdkVersion from nuget.org"
    Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/$lower/$WdkVersion/$lower.$WdkVersion.nupkg" -OutFile $zip
    Expand-Archive $zip -DestinationPath $dest
}

# --- Compile -------------------------------------------------------------------
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$msbuild = & $vswhere -latest -products * -requires Component.Microsoft.Windows.DriverKit -find 'MSBuild\**\Bin\amd64\MSBuild.exe' | Select-Object -First 1
if (-not $msbuild) {
    throw 'Visual Studio with the "Windows Driver Kit" component was not found. Add it in the Visual Studio Installer (Individual components).'
}
Invoke-Tool $msbuild @((Join-Path $root 'NoiseMic\NoiseMic.vcxproj'), '/t:Rebuild', "/p:Configuration=$Configuration", '/p:Platform=x64', '/m', '/nologo', '/v:minimal')
$built = Join-Path $buildDir $Configuration

# --- Package -------------------------------------------------------------------
if (Test-Path $packageDir) { Get-ChildItem $packageDir | Remove-Item -Recurse -Force }
New-Item -ItemType Directory -Force $packageDir | Out-Null
Copy-Item (Join-Path $built 'NoiseMic.sys') $packageDir

# Windows reads non-ASCII INF strings only from UTF-16 files.
$inf = Join-Path $packageDir 'NoiseMic.inf'
$template = Get-Content (Join-Path $root 'NoiseMic\NoiseMic.inx') -Raw -Encoding utf8
[IO.File]::WriteAllText($inf, $template, [Text.UnicodeEncoding]::new($false, $true))
Invoke-Tool (Join-Path $wdkBin 'x64\stampinf.exe') @('-f', $inf, '-a', 'amd64', '-k', $KmdfVersion, '-d', '*', '-v', $Version)
Invoke-Tool (Join-Path $wdkTools 'infverif.exe') @('/w', $inf)

$signtool = Join-Path $sdkBin 'signtool.exe'
$thumbprint = $null
$timestamp = @()
if ($NewTestCertificate) {
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject 'CN=NoiseMic Test' -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(2)
    $thumbprint = $cert.Thumbprint
    Export-Certificate -Cert $cert -FilePath (Join-Path $packageDir 'NoiseMicTest.cer') | Out-Null
    Write-Host "Created test certificate $thumbprint"
} elseif ($CertificateThumbprint) {
    $thumbprint = $CertificateThumbprint -replace '\s', ''
    $timestamp = @('/tr', $TimestampUrl, '/td', 'sha256')
}
$sign = { param($file) Invoke-Tool $signtool (@('sign', '/fd', 'sha256', '/sha1', $thumbprint) + $timestamp + @($file)) }

if ($thumbprint) { & $sign (Join-Path $packageDir 'NoiseMic.sys') }
Invoke-Tool (Join-Path $wdkBin 'x86\Inf2Cat.exe') @("/driver:$packageDir", "/os:$TargetOs")
if ($thumbprint) { & $sign (Join-Path $packageDir 'NoiseMic.cat') }

# --- Attestation submission ----------------------------------------------------
if ($Cab) {
    $ddf = Join-Path $buildDir 'NoiseMic.ddf'
    @(
        '.OPTION EXPLICIT'
        '.Set CabinetFileCountThreshold=0'
        '.Set FolderFileCountThreshold=0'
        '.Set FolderSizeThreshold=0'
        '.Set MaxCabinetSize=0'
        '.Set MaxDiskFileCount=0'
        '.Set MaxDiskSize=0'
        '.Set CompressionType=MSZIP'
        '.Set Cabinet=on'
        '.Set Compress=on'
        '.Set CabinetNameTemplate=NoiseMic.cab'
        ".Set DiskDirectoryTemplate=`"$buildDir`""
        '.Set DestinationDir=NoiseMic'
        "`"$inf`""
        "`"$(Join-Path $packageDir 'NoiseMic.sys')`""
        "`"$(Join-Path $built 'NoiseMic.pdb')`""
    ) | Set-Content $ddf -Encoding ascii
    Push-Location $buildDir
    try { Invoke-Tool 'makecab.exe' @('/F', $ddf) } finally { Pop-Location }
    $cabFile = Join-Path $buildDir 'NoiseMic.cab'
    if ($thumbprint -and -not $NewTestCertificate) {
        & $sign $cabFile
    } else {
        Write-Warning 'Sign NoiseMic.cab with your EV certificate before uploading it to Partner Center.'
    }
    Write-Host "Submission file: $cabFile"
}

Write-Host "Driver package: $packageDir"
Get-ChildItem $packageDir | ForEach-Object { Write-Host "  $($_.Name)" }
