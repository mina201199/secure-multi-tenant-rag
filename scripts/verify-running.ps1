param([string]$BaseUrl = "http://127.0.0.1:8008")
$ErrorActionPreference = "Stop"

function Assert-Status {
    param([string]$Path, [int]$Expected, $Session)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl$Path" -WebSession $Session
        $actual = [int]$response.StatusCode
    } catch {
        if (-not $_.Exception.Response) { throw }
        $actual = [int]$_.Exception.Response.StatusCode
    }
    if ($actual -ne $Expected) { throw "$Path expected $Expected, received $actual" }
}

$health = Invoke-RestMethod -Uri "$BaseUrl/health"
if ($health.status -ne "ok") { throw "Health check failed" }
$checks = 0
foreach ($company in @("a", "b")) {
    $base = if ($company -eq "a") { 1000 } else { 2000 }
    foreach ($suffix in @("admin", "supervisor", "owner", "reader", "outsider")) {
        $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
        $login = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/login" -WebSession $session
        $csrf = [regex]::Match($login.Content, 'name="csrf_token" value="([^"]+)"').Groups[1].Value
        $page = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/login" -Method Post -WebSession $session -Headers @{Origin=$BaseUrl} -Body @{
            username = "$company.$suffix"
            password = "Demo-Rag-2026!"
            csrf_token = $csrf
        }
        if ($page.StatusCode -ne 200) { throw "Login failed" }
        $me = Invoke-RestMethod -Uri "$BaseUrl/api/me" -WebSession $session
        if ($me.username -ne "$company.$suffix") { throw "Wrong session identity" }
        $offsets = switch ($suffix) { "owner" { @(1,2,3) }; "reader" { @(1,3) }; default { @(1) } }
        $expected = @($offsets | ForEach-Object { $base + $_ })
        $documents = @(Invoke-RestMethod -Uri "$BaseUrl/api/documents?tenant_id=999&user_id=103" -WebSession $session)
        $actualIds = ($documents.id | Sort-Object) -join ","
        $expectedIds = ($expected | Sort-Object) -join ","
        if ($actualIds -ne $expectedIds) {
            throw "Document authorization mismatch for $company.$suffix : expected $expectedIds, received $actualIds"
        }
        foreach ($id in @(1001,1002,1003,2001,2002,2003,999999)) {
            $status = if ($expected -contains $id) { 200 } else { 404 }
            Assert-Status "/api/documents/$id" $status $session
            Assert-Status "/documents/$id" $status $session
            $checks += 2
        }
        $csrf = [regex]::Match($page.Content, 'name="csrf_token" value="([^"]+)"').Groups[1].Value
        $null = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/logout" -Method Post -WebSession $session -Headers @{Origin=$BaseUrl} -Body @{csrf_token=$csrf}
        Assert-Status "/api/me" 401 $session
        Write-Output "PASS $company.$suffix"
    }
}
Write-Output "PASS health, 10 account logins/lists/logouts, $checks document API/page access checks"
