@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ==============================
rem  CoProducer Audio Analysis - Mastering Script
rem  Purpose: Apply broadcast-safe loudness normalization
rem  Requirements: ffmpeg, sox (for peak detection), and standard utilities
rem ==============================

rem Target integrated LUFS for broadcast compliance
set "TARGET_LUFS=-14"

rem Temporary processing directory (isolated from original files)
set "WORK_DIR=%~dp0_work"
if not exist "%WORK_DIR%" mkdir "%WORK_DIR%"

rem Process all supported audio files
for %%F in (*.wav *.mp3 *.flac) do (
    echo Processing "%%F" ...

    rem -- 1. Measure true integrated LUFS using EBU R128 -----------------
    for /f "delims=" %%L in ('
        ffmpeg -hide_banner -i "%%F" -af "ebur128" -f null - 2^>nul ^
        ^| findstr "Integrated:" ^
        ^| findstr "[: ]" ^
        for /f "tokens=2" %%X in ("%%L") do echo %%X
    ') do set "MEASURED_LUFS=%%L"

    rem -- 2. Compare against target ------------------------------------
    if "!MEASURED_LUFS!" LSS %TARGET_LUFS% (
        echo [!MEASURED_LUFS! LUFS] < %TARGET_LUFS% LUFS – applying mastering chain

        rem -- 3. Build mastering chain ---------------------------------
        rem      loudnorm   : EBU R128 loudness normalization
        rem      dynaudnorm: Dynamic range control (transparent)
        rem      compand    : Soft-knee multiband compression
        rem      alimiter    : Final brickwall limiting
        set "MASTERING_CHAIN=loudnorm=I=%TARGET_LUFS%:TP=-1.5:LRA=11:measured_I=%MEASURED_LUFS!, ^
dynaudnorm=p=0.9:m=0.9, ^
compand=attacks=0:decays=1:points=0|0.01|0.1|0.2|1|1|0.3|0.4|1, ^
alimiter=mode=soft:level=-0.5dB"

        rem -- 4. Apply processing, write to safe temporary location -------
        set "OUT_FILE=%WORK_DIR%\mastered_%%~nF%%~xF"
        ffmpeg -y -hide_banner -i "%%F" -af "!MASTERING_CHAIN!" "!OUT_FILE!" || (
            echo [!MEASURED_LUFS! LUFS] ERROR: ffmpeg processing failed for "%%F"
            goto :continue_next
        )

        rem -- 5. Peak detection & clipping protection --------------------
        rem      Use SoX to verify peak amplitude; if >0.95 apply soft limiter
        for /f "delims=" %%P in ('
            sox "!OUT_FILE!" -n stat 2^>nul ^| findstr "Maximum amplitude"
        ') do set "PEAK_AMP=%%P"
        if defined PEAK_AMP if "!PEAK_AMP!" GTR 0.95 (
            echo [!MEASURED_LUFS! LUFS] Peak normalization applied ( !PEAK_AMP! )
            ffmpeg -y -hide_banner -i "!OUT_FILE!" -af "alimiter=mode=hard:level=-0.1dB" "!OUT_FILE!"
        )

        rem -- 6. Replace original with processed version ------------------
        move /y "!OUT_FILE!" "%%F" >nul
        echo [!MEASURED_LUFS! LUFS] MASTERING COMPLETE for "%%F"
    ) else (
        echo [!MEASURED_LUFS! LUFS] Within target – no processing needed
    )
    :continue_next
)

rem -- 7. Cleanup temporary work folder -------------------------------
if exist "%WORK_DIR%" rd "%WORK_DIR%" >nul 2>&1
echo.
echo All files processed. Temporary work folder removed.
endlocal