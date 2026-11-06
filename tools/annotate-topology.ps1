param([Parameter(Mandatory = $true)][string]$Jobs)
# Draws TopoLog captions on COPIES of GNS3 screenshots. Source pictures are only read.
# Input: JSON list of {spec, source, output} written by tools/topolog/annotate.py.
# Each job writes annotation-render.json next to its output: status "drawn" or "error".
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

function Get-Family([string]$name, [string]$fallback) {
    $candidates = @($name)
    if ($name -eq '맑은 고딕') { $candidates += 'Malgun Gothic' }
    if ($name -eq 'Malgun Gothic') { $candidates += '맑은 고딕' }
    # Qt has no "TypeWriter" family on Windows and draws GNS3 labels with its default UI font.
    if ($name -match '^TypeWriter$') { $candidates = @('Tahoma') }
    $candidates += $fallback
    foreach ($candidate in $candidates) {
        try { return New-Object System.Drawing.FontFamily($candidate) } catch { }
    }
    return [System.Drawing.FontFamily]::GenericSansSerif
}

function New-PixelFont($style, [string]$fallback) {
    $family = Get-Family ([string]$style.family) $fallback
    $weight = if ($style.bold) { [System.Drawing.FontStyle]::Bold } else { [System.Drawing.FontStyle]::Regular }
    if (-not $family.IsStyleAvailable($weight)) { $weight = [System.Drawing.FontStyle]::Regular }
    # GNS3 sizes are points on a 96 dpi scene; pixels keep the picture DPI out of the result.
    $pixels = [double]$style.size * 96.0 / 72.0
    return New-Object System.Drawing.Font($family, [single]$pixels, $weight, [System.Drawing.GraphicsUnit]::Pixel)
}

function Get-Color([string]$hex) {
    if ($hex -match '^#[0-9a-fA-F]{6}$') { return [System.Drawing.ColorTranslator]::FromHtml($hex) }
    return [System.Drawing.Color]::Black
}

function Test-Overlap($a, $b) {
    return -not ($a[0] + $a[2] -le $b[0] -or $b[0] + $b[2] -le $a[0] -or $a[1] + $a[3] -le $b[1] -or $b[1] + $b[3] -le $a[1])
}

function Test-SegmentHitsRect($s, $r) {
    # Liang-Barsky clip of segment s=[x1,y1,x2,y2] against rect r=[x,y,w,h].
    $x1 = [double]$s[0]; $y1 = [double]$s[1]; $dx = [double]$s[2] - $x1; $dy = [double]$s[3] - $y1
    $p = @(-$dx, $dx, -$dy, $dy)
    $q = @(($x1 - $r[0]), ($r[0] + $r[2] - $x1), ($y1 - $r[1]), ($r[1] + $r[3] - $y1))
    $t0 = 0.0; $t1 = 1.0
    for ($i = 0; $i -lt 4; $i++) {
        if ([math]::Abs($p[$i]) -lt 1e-9) { if ($q[$i] -lt 0) { return $false } }
        else {
            $t = $q[$i] / $p[$i]
            if ($p[$i] -lt 0) { if ($t -gt $t1) { return $false }; if ($t -gt $t0) { $t0 = $t } }
            else { if ($t -lt $t0) { return $false }; if ($t -lt $t1) { $t1 = $t } }
        }
    }
    return $true
}

function Get-PlainPath([string]$Path) {
    # Windows PowerShell rejects \\?\ paths ("path format is not supported").
    if ($Path.StartsWith('\\?\UNC\')) { return '\\' + $Path.Substring(8) }
    if ($Path.StartsWith('\\?\')) { return $Path.Substring(4) }
    return $Path
}

function Invoke-Annotation($request, [string]$Source, [string]$Output) {
    $Source = Get-PlainPath $Source; $Output = Get-PlainPath $Output
    # Read through a memory copy so the GNS3 picture is never held open or locked.
    $sourceStream = New-Object System.IO.MemoryStream(,[System.IO.File]::ReadAllBytes($Source))
    $image = [System.Drawing.Image]::FromStream($sourceStream)
    $measure = New-Object System.Drawing.Bitmap(1, 1)
    $probe = [System.Drawing.Graphics]::FromImage($measure)
    $format = New-Object System.Drawing.StringFormat([System.Drawing.StringFormat]::GenericTypographic)
    $format.Alignment = [System.Drawing.StringAlignment]::Center
    # GenericTypographic drops a last line that does not fit the box exactly (LineLimit); keep every caption line.
    $format.FormatFlags = $format.FormatFlags -band (-bnot [System.Drawing.StringFormatFlags]::LineLimit)
    $labelFont = New-PixelFont $request.label_font 'Tahoma'
    $titleFont = New-PixelFont $request.title_font 'Malgun Gothic'

    $width = $image.Width; $height = $image.Height
    $top = 0
    $titleSize = $null
    if ($request.title) {
        $titleSize = $probe.MeasureString([string]$request.title, $titleFont, 4000, $format)
        $top = [int][math]::Ceiling($titleSize.Height + 24)
    }
    $legendLines = @()
    if ($request.mode -eq 'legend') { $legendLines = @($request.legend | Where-Object { $_ }) }
    $lineHeight = [int][math]::Ceiling($probe.MeasureString('Ag', $labelFont, 4000, $format).Height + 4)
    # No TopoLog mark is drawn (not requested); space below the picture is only for the legend lines.
    $bottom = $(if ($legendLines.Count) { 20 + $lineHeight * $legendLines.Count } else { 0 })

    $canvas = New-Object System.Drawing.Bitmap($width, ($top + $height + $bottom), [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $graphics = [System.Drawing.Graphics]::FromImage($canvas)
    $graphics.Clear([System.Drawing.Color]::White)
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.DrawImage($image, 0, $top, $image.Width, $image.Height)
    $ink = New-Object System.Drawing.SolidBrush((Get-Color ([string]$request.label_font.color)))

    $placed = New-Object System.Collections.ArrayList
    $obstacles = @($request.obstacles | Where-Object { $_ -ne $null })
    $segments = @($request.segments | Where-Object { $_ -ne $null })
    $overlaps = 0
    foreach ($item in @($request.items | Where-Object { $_ -ne $null })) {
        $size = $probe.MeasureString([string]$item.text, $labelFont, 4000, $format)
        $w = [double]$size.Width + 2; $h = [double]$size.Height
        $dx = [double]$item.direction[0]; $dy = [double]$item.direction[1]
        $gap = [double]$item.gap
        $rect = $null; $clear = $false; $tries = 0
        for ($tries = 0; $tries -lt 10; $tries++) {
            $reach = $gap + [math]::Abs($dx) * $w / 2 + [math]::Abs($dy) * $h / 2
            $cx = [double]$item.anchor[0] + $dx * $reach; $cy = [double]$item.anchor[1] + $dy * $reach
            $x = [math]::Min([math]::Max(2, $cx - $w / 2), $width - $w - 2)
            $y = [math]::Min([math]::Max(2, $cy - $h / 2), $height - $h - 2)
            $rect = @($x, $y, $w, $h)
            $clear = $true
            foreach ($o in $obstacles) { if (Test-Overlap $rect $o) { $clear = $false; break } }
            if ($clear) { foreach ($s in $segments) { if (Test-SegmentHitsRect $s $rect) { $clear = $false; break } } }
            if ($clear) { foreach ($p in $placed) { if (Test-Overlap $rect $p.rect) { $clear = $false; break } } }
            if ($clear) { break }
            $gap += 6
        }
        if (-not $clear) { $overlaps++ }
        $layout = New-Object System.Drawing.RectangleF([single]$rect[0], [single]($rect[1] + $top), [single]$rect[2], [single]($rect[3] + 6))
        $graphics.DrawString([string]$item.text, $labelFont, $ink, $layout, $format)
        [void]$placed.Add([pscustomobject]@{ text = [string]$item.text; rect = $rect; moved = $tries; clear = $clear })
    }

    if ($titleSize) {
        $titleBrush = New-Object System.Drawing.SolidBrush((Get-Color ([string]$request.title_font.color)))
        $titleRect = New-Object System.Drawing.RectangleF(0, 12, $width, [single]($titleSize.Height + 4))
        $graphics.DrawString([string]$request.title, $titleFont, $titleBrush, $titleRect, $format)
    }
    if ($legendLines.Count) {
        $left = New-Object System.Drawing.StringFormat([System.Drawing.StringFormat]::GenericTypographic)
        $y = $top + $height + 8
        foreach ($line in $legendLines) {
            $graphics.DrawString([string]$line, $labelFont, $ink, (New-Object System.Drawing.PointF(10, [single]$y)), $left)
            $y += $lineHeight
        }
    }

    $graphics.Dispose(); $image.Dispose(); $sourceStream.Dispose()
    $canvas.Save($Output, [System.Drawing.Imaging.ImageFormat]::Png)
    $canvas.Dispose(); $probe.Dispose(); $measure.Dispose()
    $record = [ordered]@{
        status = 'drawn'; mode = $request.mode; width = $width; height = ($top + $height + $bottom); title_space = $top
        label_font = $labelFont.FontFamily.Name; title_font = $titleFont.FontFamily.Name
        label_pixels = $labelFont.Size; overlaps = $overlaps; items = @($placed)
    }
    return $record
}

# Windows PowerShell's ConvertFrom-Json emits a JSON array as ONE object, so @(... | ConvertFrom-Json)
# held a single item whose .output joined every job's path ("path format is not supported", 15-03-12 and
# 15-29-01 runs with 5 pictures; one-picture checks passed). Pipe it once more to enumerate the jobs.
$parsed = Get-Content -LiteralPath $Jobs -Raw -Encoding UTF8 | ConvertFrom-Json
$list = @($parsed | ForEach-Object { $_ })
foreach ($job in $list) {
    $resultFile = Join-Path (Split-Path -Parent (Get-PlainPath ([string]$job.output))) 'annotation-render.json'
    try {
        $result = Invoke-Annotation $job.spec ([string]$job.source) ([string]$job.output)
    } catch {
        $result = [ordered]@{ status = 'error'; error = $_.Exception.Message }
    }
    $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resultFile -Encoding UTF8
}
