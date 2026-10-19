param([Parameter(Mandatory=$true)][string]$InputDocument,[Parameter(Mandatory=$true)][string]$OutputPdf)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$phaseDocument=(Resolve-Path -LiteralPath $InputDocument).Path
$phasePdf=[System.IO.Path]::GetFullPath($OutputPdf)
$phaseWord=$null
$phaseOpenDocument=$null
$phaseNoSave=0
try {
    $phaseWord=New-Object -ComObject Word.Application
    $phaseWord.Visible=$false
    $phaseWord.DisplayAlerts=0
    $phaseWord.AutomationSecurity=3
    $phaseOpenDocument=$phaseWord.Documents.Open($phaseDocument,$false,$true,$false)
    $phaseOpenDocument.Fields.Update() | Out-Null
    $phaseOpenDocument.Repaginate()
    $phaseOpenDocument.ExportAsFixedFormat($phasePdf,17)
    Write-Output ('Rendered native Word PDF: '+$phasePdf)
} finally {
    try {
        if ($null -ne $phaseOpenDocument) { $phaseOpenDocument.Close([ref]$phaseNoSave) }
    } finally {
        if ($null -ne $phaseOpenDocument) { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($phaseOpenDocument) }
        if ($null -ne $phaseWord) {
            try { $phaseWord.Quit([ref]$phaseNoSave) }
            finally { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($phaseWord) }
        }
    }
}
