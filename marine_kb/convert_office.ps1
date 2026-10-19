param(
    [string]$InputPath,
    [string]$OutputPath,
    [string]$OwnerPath,
    [string]$CleanupOwner,
    [string]$BatchManifest
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

if ($CleanupOwner) {
    if (-not (Test-Path -LiteralPath $CleanupOwner -PathType Leaf)) { exit 0 }
    $owned = Get-Content -LiteralPath $CleanupOwner -Raw | ConvertFrom-Json
    if (-not $owned.pid -or -not $owned.startTicks) { throw 'Missing owned process identity' }
    $proc = Get-Process -Id ([int]$owned.pid) -ErrorAction SilentlyContinue
    if ($null -eq $proc) { exit 0 }
    if ($proc.ProcessName -ne 'WINWORD' -or [string]$proc.StartTime.ToUniversalTime().Ticks -ne [string]$owned.startTicks) { throw 'Process identity changed; refusing cleanup' }
    Stop-Process -Id $proc.Id -ErrorAction Stop
    exit 0
}

if (-not $OwnerPath) { throw 'Owner path required' }
$ownerResolved = [IO.Path]::GetFullPath($OwnerPath)
$temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
if (-not $ownerResolved.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Owner record must stay within temporary directory' }
if ($BatchManifest) {
    $items = Get-Content -LiteralPath $BatchManifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($items.Count -lt 1 -or $items.Count -gt 50) { throw 'Batch must have 1 to 50 items' }
} else {
    if (-not $InputPath -or -not $OutputPath) { throw 'Input and output paths required' }
    $items = @([pscustomobject]@{input=$InputPath; output=$OutputPath})
}
$validatedItems = @()
foreach ($item in $items) {
    $inputResolved = (Resolve-Path -LiteralPath $item.input -ErrorAction Stop).Path
    $outputResolved = [IO.Path]::GetFullPath($item.output)
    if (-not $outputResolved.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Conversion output must stay within temporary directory' }
    if ($inputResolved -eq $outputResolved -or (Test-Path -LiteralPath $outputResolved)) { throw 'Refusing source modification or output overwrite' }
    if ([IO.Path]::GetExtension($outputResolved) -ne '.docx') { throw 'Only docx conversion output allowed' }
    if ((Get-Item -LiteralPath $inputResolved).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing linked source' }
    $validatedItems += [pscustomobject]@{input=$inputResolved; output=$outputResolved}
}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class MarineWordOwner {
  [DllImport("user32.dll")]
  public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
}
'@

$previousIds = @(Get-Process -Name WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
$word = $null
$document = $null
$identityDocument = $null
$ownsWord = $false
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $word.Options.UpdateLinksAtOpen = $false
    $word.Options.SaveNormalPrompt = $false
    # Word does not expose Application.Hwnd consistently. A new, unsaved
    # identity document gives us the native window of this COM instance.
    $identityDocument = $word.Documents.Add()
    [uint32]$createdId = 0
    $null = [MarineWordOwner]::GetWindowThreadProcessId([IntPtr]$identityDocument.ActiveWindow.Hwnd, [ref]$createdId)
    if (-not $createdId -or $previousIds -contains [int]$createdId) { throw 'Word instance ownership not proven; conversion declined' }
    $ownedProcess = Get-Process -Id ([int]$createdId) -ErrorAction Stop
    $ownsWord = $true
    [IO.File]::WriteAllText($ownerResolved, (@{pid=[int]$createdId; startTicks=[string]$ownedProcess.StartTime.ToUniversalTime().Ticks} | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    $identityDocument.Close(0)
    $identityDocument = $null
    # ConfirmConversions=false, ReadOnly=true, AddToRecentFiles=false,
    # empty passwords (no password prompt) and Visible=false.
    foreach ($item in $validatedItems) {
        try {
            $document = $word.Documents.Open($item.input, $false, $true, $false, '', '', $false, '', '', 0, 0, $false, $true)
            $document.SaveAs2($item.output, 12, $false, '', $false, '', $false, $false, $false, $false, $false, 0, $false, $false, $false)
            Write-Output (@{input=$item.input; status='converted'} | ConvertTo-Json -Compress)
        }
        catch {
            Write-Output (@{input=$item.input; status='error'; detail=$_.Exception.Message} | ConvertTo-Json -Compress)
        }
        finally {
            if ($null -ne $document) { $document.Close(0); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document); $document = $null }
        }
    }
}
finally {
    if ($null -ne $identityDocument) { $identityDocument.Close(0) }
    if ($null -ne $document -and $ownsWord) { try { $document.Close(0) } catch {} }
    if ($null -ne $word -and $ownsWord) { try { $word.Quit() } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) } }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
