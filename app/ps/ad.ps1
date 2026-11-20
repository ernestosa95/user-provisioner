# Operaciones AD. Parametros por entorno (nunca por linea de comandos):
#   PROV_PAYLOAD  = JSON (action, server, ou, domain, username, ...)
#   PROV_PASSWORD = contrasena (create: clave del usuario nuevo | auth: clave a validar)
#   PROV_SVC_USER / PROV_SVC_PASSWORD = opcional, cuenta de servicio
# Salida: una linea JSON {"ok":bool,"result":...,"error":...} en UTF-8
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {
    $p = $env:PROV_PAYLOAD | ConvertFrom-Json
    $result = $null
    if ($p.action -eq 'auth') {
        # Login de la app: no necesita el modulo ActiveDirectory (mas rapido).
        Add-Type -AssemblyName System.DirectoryServices.AccountManagement
        $am = 'System.DirectoryServices.AccountManagement'
        $target = if ($p.server) { $p.server } else { $p.domain }
        $ctx = if ($env:PROV_SVC_USER) {
            New-Object "$am.PrincipalContext"([System.DirectoryServices.AccountManagement.ContextType]::Domain,
                $target, $env:PROV_SVC_USER, $env:PROV_SVC_PASSWORD)
        } else {
            New-Object "$am.PrincipalContext"([System.DirectoryServices.AccountManagement.ContextType]::Domain, $target)
        }
        try {
            $result = $ctx.ValidateCredentials([string]$p.username, $env:PROV_PASSWORD,
                [System.DirectoryServices.AccountManagement.ContextOptions]::Negotiate)
        } finally { $ctx.Dispose() }
        @{ ok = $true; result = [bool]$result } | ConvertTo-Json -Compress
        exit 0
    }

    Import-Module ActiveDirectory
    $common = @{}
    if ($p.server) { $common.Server = $p.server }  # vacio = DC del dominio
    if ($env:PROV_SVC_USER) {
        $sec = ConvertTo-SecureString $env:PROV_SVC_PASSWORD -AsPlainText -Force
        $common.Credential = New-Object System.Management.Automation.PSCredential($env:PROV_SVC_USER, $sec)
    }
    $u = if ($p.username) { ([string]$p.username).Replace("'", "''") } else { '' }
    $g = if ($p.group) { ([string]$p.group).Replace("'", "''") } else { '' }
    switch ($p.action) {
        'ping' {
            $dom = Get-ADDomain @common
            if ($p.ou) { $null = Get-ADObject -Identity $p.ou @common; $ou = 'OU encontrada' } else { $ou = 'OU sin configurar' }
            $result = "Conectado a $($dom.DNSRoot); $ou"
        }
        'user_exists' {
            $result = [bool](Get-ADUser -Filter "SamAccountName -eq '$u'" @common)
        }
        'group_exists' {
            $result = [bool](Get-ADGroup -Filter "Name -eq '$g'" @common)
        }
        'create' {
            $pw = ConvertTo-SecureString $env:PROV_PASSWORD -AsPlainText -Force
            $display = "$($p.first_name) $($p.last_name)"
            # CN = "Nombre Apellido (usuario)": unico en la OU aunque haya homonimos.
            New-ADUser @common -Name "$display ($($p.username))" -DisplayName $display `
                -GivenName $p.first_name -Surname $p.last_name `
                -SamAccountName $p.username -UserPrincipalName "$($p.username)@$($p.domain)" `
                -Path $p.ou -AccountPassword $pw `
                -PasswordNeverExpires ([bool]$p.never_expires) -ChangePasswordAtLogon $false `
                -Enabled $true
            $result = 'created'
        }
        'add_group' {
            # Mismo criterio de busqueda que group_exists (por Name).
            $grp = Get-ADGroup -Filter "Name -eq '$g'" @common
            if (-not $grp) { throw "El grupo '$($p.group)' no existe" }
            Add-ADGroupMember @common -Identity $grp -Members $p.username
            $result = 'added'
        }
        'delete' {
            Remove-ADUser @common -Identity $p.username -Confirm:$false
            $result = 'deleted'
        }
        default { throw "Accion desconocida: $($p.action)" }
    }
    @{ ok = $true; result = $result } | ConvertTo-Json -Compress
} catch {
    @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
}
