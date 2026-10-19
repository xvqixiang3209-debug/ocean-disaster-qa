param([Parameter(Mandatory=$true)][string]$InputDocument)
$ErrorActionPreference='Stop'
$phaseExpected=(Resolve-Path -LiteralPath $InputDocument).Path
$phaseNoSave=0
for ($phaseIndex=0;$phaseIndex -lt 3;$phaseIndex++) {
    $phaseWord=$null
    $phaseDocument=$null
    try {
        try { $phaseWord=[System.Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application') }
        catch { break }
        if ($phaseWord.Visible -or $phaseWord.Documents.Count -ne 1) { break }
        $phaseDocument=$phaseWord.Documents.Item(1)
        if (-not [string]::Equals($phaseDocument.FullName,$phaseExpected,[System.StringComparison]::OrdinalIgnoreCase)) { break }
        $phaseDocument.Close([ref]$phaseNoSave)
        $phaseWord.Quit([ref]$phaseNoSave)
        Write-Output 'Closed a hidden preview instance containing only this export.'
    } finally {
        if ($null -ne $phaseDocument) { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($phaseDocument) }
        if ($null -ne $phaseWord) { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($phaseWord) }
    }
}
