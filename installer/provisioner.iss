; Instalador de Alta de Usuarios (AD + Suitestensa) - Inno Setup 6. Se compila con build.ps1.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Alta de Usuarios (AD + Suitestensa)"
#define ServiceExe "UserProvisioner.exe"
#define DataDir "{commonappdata}\UserProvisioner"

[Setup]
AppId={{42C2D0FC-882C-4E69-AE5E-89BD3D3E800F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Medical IT (Soporte Técnico)
DefaultDirName={autopf}\UserProvisioner
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=UserProvisioner-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
CloseApplications=no
SetupLogging=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "firewall"; Description: "Abrir el puerto de la aplicación en el Firewall de Windows"
Name: "demo"; Description: "Modo DEMOSTRACIÓN: AD y SQL simulados (no usar en producción)"; Flags: unchecked

[Dirs]
Name: "{#DataDir}"
Name: "{#DataDir}\data"
Name: "{#DataDir}\logs"
Name: "{#DataDir}\certs"

[Files]
Source: "build\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "build\provisioner\*"; DestDir: "{app}\provisioner"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "build\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "build\{#ServiceExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\UserProvisioner.xml"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\provisioner-cli.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\config.toml.template"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; En una actualización se borra el código anterior (los datos y la configuración están en ProgramData).
Type: filesandordirs; Name: "{app}\provisioner"

[Icons]
Name: "{autoprograms}\{#AppName}\Abrir Alta de Usuarios"; Filename: "https://localhost:{code:GetPort}/"
Name: "{autoprograms}\{#AppName}\Configuración de instalación (config.toml)"; Filename: "notepad.exe"; Parameters: """{#DataDir}\config.toml"""
Name: "{autoprograms}\{#AppName}\Logs"; Filename: "{#DataDir}\logs"

[Run]
Filename: "https://localhost:{code:GetPort}/"; Description: "Abrir la aplicación (configuración inicial de AD y SQL)"; Flags: postinstall shellexec nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\provisioner"
Type: filesandordirs; Name: "{app}\python"

[Code]
var
  SitePage: TInputQueryWizardPage;
  ConfigExisted: Boolean;
  Warnings: String;

function ConfigFile(): String;
begin
  Result := ExpandConstant('{#DataDir}\config.toml');
end;

function GetPort(Param: String): String;
var
  Lines: TArrayOfString;
  I: Integer;
  L: String;
begin
  Result := '8444';
  if (SitePage <> nil) and (not ConfigExisted) and (Trim(SitePage.Values[1]) <> '') then
  begin
    Result := Trim(SitePage.Values[1]);
    Exit;
  end;
  if LoadStringsFromFile(ConfigFile(), Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      L := Trim(Lines[I]);
      if Pos('port', L) = 1 then
      begin
        Delete(L, 1, Pos('=', L));
        Result := Trim(L);
      end;
    end;
end;

procedure InitializeWizard();
begin
  ConfigExisted := FileExists(ConfigFile());

  SitePage := CreateInputQueryPage(wpSelectTasks, 'Sitio',
    'Datos de la instalación',
    'La conexión a Active Directory y a la base de Suitestensa se configura después, desde la propia aplicación ' +
    '(se abre al terminar la instalación).');
  SitePage.Add('Nombre del sitio (ej. HOSPITAL-XYZ):', False);
  SitePage.Add('Puerto HTTPS:', False);
  SitePage.Values[1] := '8444';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // En una actualización se conserva config.toml: no se vuelve a preguntar.
  Result := ConfigExisted and (PageID = SitePage.ID);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  P: Integer;
begin
  Result := True;
  if CurPageID = SitePage.ID then
  begin
    P := StrToIntDef(Trim(SitePage.Values[1]), 0);
    if (P < 1) or (P > 65535) then
    begin
      MsgBox('Puerto inválido.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function RunHidden(const FileName, Params: String): Integer;
var
  Code: Integer;
begin
  if not Exec(FileName, Params, ExpandConstant('{app}\provisioner'), SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := -1;
  Log(Format('Exec %s %s -> %d', [FileName, Params, Code]));
  Result := Code;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Svc: String;
begin
  Result := '';
  Svc := ExpandConstant('{app}\{#ServiceExe}');
  if FileExists(Svc) then
  begin
    RunHidden(Svc, 'stop');
    Sleep(2000);
  end;
end;

// Valor para ir entre comillas dobles en TOML (escapa \ y ").
function TomlStr(const Value: String): String;
begin
  Result := Trim(Value);
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

procedure ReplaceIn(var S: String; const Key, Value: String);
begin
  StringChangeEx(S, '{{' + Key + '}}', Value, True);
end;

procedure WriteConfigFile();
var
  S: AnsiString;
  T: String;
begin
  if not LoadStringFromFile(ExpandConstant('{app}\config.toml.template'), S) then
  begin
    MsgBox('No se pudo leer config.toml.template', mbError, MB_OK);
    Exit;
  end;
  T := String(S);
  if WizardIsTaskSelected('demo') then
    ReplaceIn(T, 'MODE', 'demo')
  else
    ReplaceIn(T, 'MODE', 'real');
  ReplaceIn(T, 'SITE_NAME', TomlStr(SitePage.Values[0]));
  ReplaceIn(T, 'PORT', Trim(SitePage.Values[1]));
  ReplaceIn(T, 'HTTPS_ONLY', 'true');
  SaveStringToFile(ConfigFile(), AnsiString(T), False);
end;

// Requisitos del modo real: se avisa, no se bloquea la instalación.
procedure CheckPrerequisites();
begin
  if not (RegKeyExists(HKLM, 'SOFTWARE\ODBC\ODBCINST.INI\ODBC Driver 18 for SQL Server') or
          RegKeyExists(HKLM, 'SOFTWARE\ODBC\ODBCINST.INI\ODBC Driver 17 for SQL Server')) then
    Warnings := Warnings + '- Falta el ODBC Driver 17/18 for SQL Server.' + #13#10;
  if RunHidden(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-NoProfile -NonInteractive -Command "if (Get-Module -ListAvailable ActiveDirectory) { exit 0 } else { exit 1 }"') <> 0 then
    Warnings := Warnings + '- Falta el módulo ActiveDirectory de PowerShell (RSAT): ' +
      'Add-WindowsCapability -Online -Name Rsat.ActiveDirectory.DS-LDS.Tools~~~~0.0.1.0 ' +
      '(en Windows Server: Install-WindowsFeature RSAT-AD-PowerShell).' + #13#10;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Py, Svc, Data, Port, Cfg: String;
begin
  if CurStep <> ssPostInstall then
    Exit;

  Py := ExpandConstant('{app}\python\python.exe');
  Svc := ExpandConstant('{app}\{#ServiceExe}');
  Data := ExpandConstant('{#DataDir}');
  Cfg := '--config "' + ConfigFile() + '"';

  WizardForm.StatusLabel.Caption := 'Configurando la aplicación...';
  if not ConfigExisted then
    WriteConfigFile();

  // Datos, clave maestra y certificado: solo Administradores y SYSTEM.
  RunHidden(ExpandConstant('{sys}\icacls.exe'),
    '"' + Data + '" /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F');

  if RunHidden(Py, '-m app.cli ' + Cfg + ' init') <> 0 then
    MsgBox('No se pudo inicializar la aplicación. Revisar config.toml y ejecutar "provisioner-cli init".', mbError, MB_OK);

  if WizardIsTaskSelected('firewall') then
  begin
    Port := GetPort('');
    RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="Alta de Usuarios"');
    RunHidden(ExpandConstant('{sys}\netsh.exe'),
      'advfirewall firewall add rule name="Alta de Usuarios" dir=in action=allow protocol=TCP localport=' + Port);
  end;

  WizardForm.StatusLabel.Caption := 'Registrando e iniciando el servicio...';
  RunHidden(Svc, 'install');  // si ya existe devuelve error y se ignora
  if RunHidden(Svc, 'start') <> 0 then
    MsgBox('El servicio no pudo iniciarse. Revisar los logs en ' + Data + '\logs y config.toml.', mbError, MB_OK);

  if not WizardIsTaskSelected('demo') then
  begin
    CheckPrerequisites();
    if Warnings <> '' then
      MsgBox('La aplicación quedó instalada, pero para crear usuarios falta:' + #13#10#13#10 + Warnings,
        mbInformation, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Svc: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    Svc := ExpandConstant('{app}\{#ServiceExe}');
    RunHidden(Svc, 'stop');
    Sleep(2000);
    RunHidden(Svc, 'uninstall');
    RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="Alta de Usuarios"');
  end;
  if CurUninstallStep = usPostUninstall then
    MsgBox('Se conservaron la configuración, la base local (admins, roles, auditoría, claves cifradas) y los logs en ' +
      ExpandConstant('{#DataDir}') + '. Borrar esa carpeta manualmente si no se van a usar más.',
      mbInformation, MB_OK);
end;
