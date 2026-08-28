; 闲鱼价格监控 - Inno Setup 安装包定义
; 编译环境：Inno Setup 6（https://jrsoftware.org/isinfo.php）
; 编译命令：ISCC.exe xianyu-monitor.iss
; 产物：Output\XianYuMonitor-Setup-x.x.x.exe
;
; 说明：
;   1. 打包项目源码（排除运行时文件）
;   2. 安装完成后调用 install.ps1 -NoElevate 创建虚拟环境、安装依赖、
;      Playwright Chromium 并注册开机自启任务（首次安装需联网下载，约 200MB）
;   3. 提供开始菜单快捷方式与卸载入口

#define MyAppName "闲鱼价格监控"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Azhi233"
#define MyAppExeName "app.py"
#define MyAppURL "https://github.com/Azhi233/what-to-buy-today"

[Setup]
; AppId 唯一标识，勿与其他应用重复
AppId={{8F3E2C5A-7D41-4B6E-9A28-6C2D5F1B8E93}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\XianYuMonitor
; 安装界面提供目录选择页（用户可自由选择安装位置）
DisableDirPage=no
DefaultGroupName={#MyAppName}
OutputDir=Output
OutputBaseFilename=XianYuMonitor-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
CloseApplications=yes
; 安装后自动清理（避免卸载残留计划任务）
UsePreviousAppDir=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "run.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "monitor.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "monitor_service.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "database.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "notifier.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "tray.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements-dev.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "templates\*"; DestDir: "{app}\templates"; Flags: recursesubdirs ignoreversion
Source: "static\*"; DestDir: "{app}\static"; Flags: recursesubdirs ignoreversion
Source: "install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "uninstall.ps1"; DestDir: "{app}"; Flags: ignoreversion
; 排除运行时文件（.venv/data/browser_profile/日志由安装脚本创建）

[Icons]
Name: "{group}\打开监控仪表盘"; Filename: "http://127.0.0.1:5000"
Name: "{group}\重新登录闲鱼"; Filename: "{app}\.venv\Scripts\python.exe"; Parameters: "{app}\run.py --login"
Name: "{group}\启动托盘监控"; Filename: "{app}\.venv\Scripts\python.exe"; Parameters: "{app}\tray.py"
Name: "{group}\查看文档"; Filename: "https://github.com/Azhi233/what-to-buy-today#readme"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "http://127.0.0.1:5000"; Tasks: desktopicon

[Run]
; 安装依赖 + Chromium + 注册开机自启（耗时取决于网络，可长达数分钟）
Filename: "{cmd}"; Parameters: "/d ""{app}"" /c powershell -NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"" -NoElevate"; Flags: runhidden waituntilterminated; StatusMsg: "正在安装运行依赖并注册开机自启（首次约 200MB，请耐心等待）..."
Filename: "http://127.0.0.1:5000"; Description: "打开监控仪表盘"; Flags: shellexec postinstall nowait skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/d ""{app}"" /c powershell -NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall.ps1"" -NoElevate"; Flags: runhidden; RunOnceId: "XianYuUninstall"
