param([Parameter(Mandatory=$true)][string]$InputDocx,[Parameter(Mandatory=$true)][string]$OutputPdf)
$ErrorActionPreference='Stop'
$inputPath=(Resolve-Path -LiteralPath $InputDocx).Path
$outputPath=[IO.Path]::GetFullPath($OutputPdf)
if([IO.Path]::GetExtension($inputPath) -ne '.docx'){throw 'Only the generated DOCX is supported'}
$wordInstance=$null
$ownDocument=$null
try {
    $wordInstance=New-Object -ComObject Word.Application
    $wordInstance.Visible=$false
    $wordInstance.DisplayAlerts=0
    $wordInstance.AutomationSecurity=3
    $ownDocument=$wordInstance.Documents.Open($inputPath,$false,$true,$false)
    $ownDocument.ExportAsFixedFormat($outputPath,17)
    Write-Output 'Generated report exported for visual verification'
} finally {
    if($null -ne $ownDocument){$ownDocument.Close(0);[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($ownDocument)}
    if($null -ne $wordInstance){$wordInstance.Quit(0);[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordInstance)}
}
