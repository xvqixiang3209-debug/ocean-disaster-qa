param(
    [string]$BaseUrl = "http://127.0.0.1:8766"
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$exampleFile = Join-Path $projectRoot "typhoon\examples\validation\current_lekima_full_track_summary_link_test.json"

Write-Output "Checking service status..."
$status = Invoke-RestMethod -Uri "$BaseUrl/api/typhoon/status" -Method Get
$status | ConvertTo-Json -Depth 6

Write-Output "Running an end-to-end sample request..."
$body = Get-Content -LiteralPath $exampleFile -Raw -Encoding UTF8
$result = Invoke-RestMethod `
    -Uri "$BaseUrl/api/typhoon/similar" `
    -Method Post `
    -ContentType "application/json" `
    -Body $body

$summary = [ordered]@{
    request_id = $result.request_id
    analogue_count = @($result.analogues).Count
    rag_available = $status.rag.available
    rag_chunk_count = $status.rag.chunk_count
    report_knowledge_available = $status.report_knowledge.available
    report_count = $status.report_knowledge.report_count
    first_analogue = if (@($result.analogues).Count) { "$(($result.analogues)[0].season) $(($result.analogues)[0].name)" } else { $null }
    first_similarity = if (@($result.analogues).Count) { ($result.analogues)[0].similarity_score } else { $null }
    first_has_historical_report = if (@($result.analogues).Count) { [bool](($result.analogues)[0].historical_report) } else { $false }
    processing_ms = $result.processing_ms
    requested_mode = $result.report_meta.requested_mode
    used_mode = $result.report_meta.used_mode
    fallback = $result.report_meta.fallback
    fallback_reason = $result.report_meta.fallback_reason
    fact_validation = $result.report_meta.fact_validation
}
$summary | Format-List
