#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$CollectionDirectory = '',
    [string]$ProjectSnapshotDirectory = '',
    [string]$TopologyImagePath = '',
    [string]$TopologyImageSource = '',
    [string]$TopologyCaptureMetadataPath = '',
    [string]$ResultPath = ''
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.IO.Compression.FileSystem
. (Join-Path $PSScriptRoot 'project-paths.ps1')
$projectPaths=Get-TopoLogPaths -SourceRoot (Split-Path -Parent $PSScriptRoot)
$root = $projectPaths.DataRoot
if (-not $CollectionDirectory) {
    $latest = Get-ChildItem -LiteralPath (Join-Path $root 'work') -Directory -Filter 'console_*' |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'collection.json') } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { throw 'No console collection found.' }
    $CollectionDirectory = $latest.FullName
}
$collection = Get-Content -LiteralPath (Join-Path $CollectionDirectory 'collection.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if (-not $collection.collection_complete) { throw 'Choose a complete collection for this export prototype.' }
$collectionStartedIso = ([DateTimeOffset]$collection.collection_started_at).ToString('o')
$collectionFinishedIso = ([DateTimeOffset]$collection.finished_at).ToString('o')
$projectName = [string]$collection.project_name
if ($projectName -notmatch '^[A-Za-z0-9 _-]+$') { throw 'Unsupported project filename.' }
if(-not $ProjectSnapshotDirectory){$ProjectSnapshotDirectory=Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($CollectionDirectory))) 'project'}
$snapshotPath=Join-Path $ProjectSnapshotDirectory 'snapshot.json'
if(-not (Test-Path -LiteralPath $snapshotPath)){throw 'Current project snapshot missing. Run collect-project.ps1 first; development reference archives are not runtime inputs.'}
$snapshot=Get-Content -LiteralPath $snapshotPath -Raw -Encoding UTF8 | ConvertFrom-Json
if($snapshot.schema -ne 'topolog-project-snapshot-v1' -or -not $snapshot.complete -or
   $snapshot.project_id -ne $collection.project_id -or $snapshot.project_name -ne $projectName -or
   $snapshot.topology_file -ne 'topology.json'){throw 'The project snapshot is incomplete or belongs to a different project.'}
$topologyPath=Join-Path $ProjectSnapshotDirectory 'topology.json'
if((Get-FileHash -LiteralPath $topologyPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $snapshot.topology_sha256){throw 'Project snapshot topology hash mismatch.'}
$topology=Get-Content -LiteralPath $topologyPath -Raw -Encoding UTF8 | ConvertFrom-Json
if($topology.project_id -ne $collection.project_id -or $collection.node_id -notin @($topology.topology.nodes | ForEach-Object node_id)){throw 'Project or node ID differs from the console collection.'}
$stamp=(Get-Date -Format 'yyyyMMdd_HHmmss')+'_'+[guid]::NewGuid().ToString('N').Substring(0,6)
$outDir=Join-Path $root ('exports\TopoLog_'+$projectName+'_'+$stamp)
foreach($folder in @('','assets','logs','configs','source')){[IO.Directory]::CreateDirectory((Join-Path $outDir $folder))|Out-Null}
$utf8=[Text.UTF8Encoding]::new($false)
Copy-Item -LiteralPath $topologyPath -Destination (Join-Path $outDir 'source\topology.json')
Copy-Item -LiteralPath $snapshotPath -Destination (Join-Path $outDir 'source\project-snapshot.json')
Get-ChildItem -LiteralPath $CollectionDirectory -File | Copy-Item -Destination (Join-Path $outDir 'logs')
$configSources=@()
foreach($cfg in @($snapshot.configurations)) {
    $exportedFile=$null
    if($cfg.status -eq 'collected') {
        if($cfg.file -notmatch '^configs/[a-fA-F0-9-]{36}_startup\.cfg$'){throw 'Unsupported snapshot configuration path.'}
        $inputFile=Join-Path $ProjectSnapshotDirectory $cfg.file
        if((Get-FileHash -LiteralPath $inputFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $cfg.sha256){throw 'Snapshot configuration hash mismatch.'}
        Copy-Item -LiteralPath $inputFile -Destination (Join-Path $outDir $cfg.file)
        $exportedFile=$cfg.file
    }
    $configSources+=[pscustomobject]@{node_name=$cfg.node_name;node_id=$cfg.node_id;status=$cfg.status;reason=$cfg.reason;source_endpoint=$cfg.endpoint;exported_file=$exportedFile;collected_at=$cfg.collected_at}
}
$configCollected=@($configSources | Where-Object status -eq 'collected').Count
$configMissing=@($configSources | Where-Object status -ne 'collected')
$configStatusLines=(@($configSources | ForEach-Object { '- '+$_.node_name+': '+$_.status+$(if($_.reason){' — '+$_.reason}) })) -join "`n"

function New-TextImage([string]$Text, [string]$Title, [string]$Destination) {
    $lines = @($Text.Replace("`r`n", "`n").Split("`n"))
    $font = [Drawing.Font]::new('Consolas',16,[Drawing.FontStyle]::Regular,[Drawing.GraphicsUnit]::Pixel)
    $titleFont = [Drawing.Font]::new('Segoe UI',21,[Drawing.FontStyle]::Bold,[Drawing.GraphicsUnit]::Pixel)
    $smallFont = [Drawing.Font]::new('Segoe UI',13,[Drawing.FontStyle]::Regular,[Drawing.GraphicsUnit]::Pixel)
    $probeImage = [Drawing.Bitmap]::new(1,1)
    $probeGraphics = [Drawing.Graphics]::FromImage($probeImage)
    try {
        $width = 1000
        foreach ($line in $lines) { $width = [Math]::Max($width, [int][Math]::Ceiling($probeGraphics.MeasureString($line,$font).Width) + 72) }
        $height = 105 + $lines.Count * 25 + 65
    } finally { $probeGraphics.Dispose(); $probeImage.Dispose() }
    $image = [Drawing.Bitmap]::new($width,$height)
    $g = [Drawing.Graphics]::FromImage($image)
    try {
        $g.Clear([Drawing.Color]::White)
        $g.TextRenderingHint = [Drawing.Text.TextRenderingHint]::ClearTypeGridFit
        $g.DrawString($Title,$titleFont,[Drawing.Brushes]::Black,28,20)
        $g.DrawString(('Project ' + $projectName + ' | Node ' + $collection.node_name + ' | ' + $collectionStartedIso),$smallFont,[Drawing.Brushes]::DimGray,28,53)
        $g.FillRectangle([Drawing.Brushes]::WhiteSmoke,20,88,$width-40,$lines.Count*25+12)
        $y=94
        foreach($line in $lines) { $g.DrawString($line,$font,[Drawing.Brushes]::Black,30,$y); $y+=25 }
        $g.DrawString('Generated from recorded console text. This image is not a screen capture.',$smallFont,[Drawing.Brushes]::DimGray,28,$height-35)
        $image.Save($Destination,[Drawing.Imaging.ImageFormat]::Png)
    } finally { $g.Dispose(); $image.Dispose(); $font.Dispose(); $titleFont.Dispose(); $smallFont.Dispose() }
}

# Reconstruct stored geometry and links; original GNS3 symbols are replaced by
# simple router symbols. Source SVG text annotations are retained as text.
$nodes = @($topology.topology.nodes)
$annotations = @()
foreach($drawing in @($topology.topology.drawings)) {
    [xml]$svg = $drawing.svg
    $textNodes = @($svg.SelectNodes('//*[local-name()="text"]'))
    $annotationText = ($textNodes | ForEach-Object { $_.InnerText }) -join ' '
    if($annotationText.Trim()) { $annotations += [pscustomobject]@{ x=[double]$drawing.x; y=[double]$drawing.y; text=$annotationText; width=[double]$svg.DocumentElement.GetAttribute('width'); height=[double]$svg.DocumentElement.GetAttribute('height') } }
}
$allX = @($nodes | ForEach-Object { [double]$_.x }) + @($annotations | ForEach-Object x)
$allY = @($nodes | ForEach-Object { [double]$_.y }) + @($annotations | ForEach-Object y)
$minX = ($allX | Measure-Object -Minimum).Minimum - 70
$minY = ($allY | Measure-Object -Minimum).Minimum - 20
$maxX = ($allX | Measure-Object -Maximum).Maximum + 220
$maxY = ($allY | Measure-Object -Maximum).Maximum + 100
$scale = 1.25
$imageWidth = [Math]::Max(1100,[int](($maxX-$minX)*$scale)+80)
$imageHeight = [Math]::Max(850,[int](($maxY-$minY)*$scale)+170)
$topImage = [Drawing.Bitmap]::new($imageWidth,$imageHeight)
$g = [Drawing.Graphics]::FromImage($topImage)
$heading = [Drawing.Font]::new('Segoe UI',24,[Drawing.FontStyle]::Bold,[Drawing.GraphicsUnit]::Pixel)
$font = [Drawing.Font]::new('Segoe UI',17,[Drawing.FontStyle]::Regular,[Drawing.GraphicsUnit]::Pixel)
$noteFont = [Drawing.Font]::new('Malgun Gothic',15,[Drawing.FontStyle]::Regular,[Drawing.GraphicsUnit]::Pixel)
$pen = [Drawing.Pen]::new([Drawing.Color]::FromArgb(90,105,125),3)
$nodePen = [Drawing.Pen]::new([Drawing.Color]::FromArgb(38,67,95),2)
$nodeBrush = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(232,241,249))
try {
    $g.Clear([Drawing.Color]::White)
    $g.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [Drawing.Text.TextRenderingHint]::ClearTypeGridFit
    $g.DrawString(('API topology snapshot | ' + $projectName),$heading,[Drawing.Brushes]::Black,28,20)
    $g.DrawString('Source: GNS3 API geometry, links and annotations. Not a screenshot.',$noteFont,[Drawing.Brushes]::DimGray,28,60)
    $positions = @{}
    foreach($node in $nodes) { $positions[$node.node_id] = [pscustomobject]@{ x=40+([double]$node.x+[double]$node.width/2-$minX)*$scale; y=110+([double]$node.y+[double]$node.height/2-$minY)*$scale } }
    foreach($link in @($topology.topology.links)) {
        if(@($link.nodes).Count -ne 2) { throw 'Only two-ended links are supported by this rendering prototype.' }
        $a=$positions[$link.nodes[0].node_id]; $b=$positions[$link.nodes[1].node_id]
        if(-not $a -or -not $b) { throw 'A link references a missing node.' }
        $g.DrawLine($pen,[single]$a.x,[single]$a.y,[single]$b.x,[single]$b.y)
        for($i=0;$i -lt 2;$i++) {
            $t=0.20; if($i -eq 1){$t=0.80}
            $label=[string]$link.nodes[$i].label.text
            if(-not $label){$label='adapter '+$link.nodes[$i].adapter_number+'/port '+$link.nodes[$i].port_number}
            $size=$g.MeasureString($label,$font)
            $x=$a.x+($b.x-$a.x)*$t-$size.Width/2; $y=$a.y+($b.y-$a.y)*$t+6
            $g.FillRectangle([Drawing.Brushes]::White,[single]($x-3),[single]$y,[single]($size.Width+6),[single]$size.Height)
            $g.DrawString($label,$font,[Drawing.Brushes]::Black,[single]$x,[single]$y)
        }
    }
    foreach($node in $nodes) {
        $pos=$positions[$node.node_id]
        $g.FillEllipse($nodeBrush,[single]($pos.x-38),[single]($pos.y-26),76,52)
        $g.DrawEllipse($nodePen,[single]($pos.x-38),[single]($pos.y-26),76,52)
        $size=$g.MeasureString([string]$node.name,$font)
        $g.DrawString([string]$node.name,$font,[Drawing.Brushes]::Black,[single]($pos.x-$size.Width/2),[single]($pos.y-$size.Height/2))
    }
    foreach($a in $annotations) { $g.DrawString($a.text,$noteFont,[Drawing.Brushes]::DimGray,[single](40+($a.x-$minX)*$scale),[single](110+($a.y-$minY)*$scale)) }
    $g.DrawString('Node and project IDs match the collected session. Saved files and runtime evidence have separate timestamps.',$noteFont,[Drawing.Brushes]::DimGray,28,$imageHeight-45)
    $topImage.Save((Join-Path $outDir 'assets\topology-reconstructed.png'),[Drawing.Imaging.ImageFormat]::Png)
} finally { $g.Dispose();$topImage.Dispose();$heading.Dispose();$font.Dispose();$noteFont.Dispose();$pen.Dispose();$nodePen.Dispose();$nodeBrush.Dispose() }

$topologyImageProvenance = $null
$topologyDisplayPath='assets/topology-reconstructed.png'
$topologyDisplayKind='저장 파일로 재구성한 그림'
$topologySection = @'
## 저장 파일 기반 토폴로지

![저장된 프로젝트 파일로 재구성한 토폴로지](assets/topology-reconstructed.png)

실제 GNS3 화면 캡처가 아닙니다. 저장된 장비 배치와 연결을 다시 그렸으며 장비 아이콘은 단순화했습니다. 원본의 텍스트 주석을 보존했으므로 주석의 기술 명칭 자체를 실제 실행 결과로 간주하지 않습니다.
'@
if ($TopologyImagePath) {
    $extension = [IO.Path]::GetExtension($TopologyImagePath).ToLowerInvariant()
    if ($extension -notin @('.png','.jpg','.jpeg','.bmp')) { throw 'Supported topology image extensions: PNG, JPG, JPEG, BMP.' }
    $providedImage = [Drawing.Image]::FromFile([IO.Path]::GetFullPath($TopologyImagePath))
    try { $imageDimensions = [pscustomobject]@{ width=$providedImage.Width; height=$providedImage.Height } }
    finally { $providedImage.Dispose() }
    $imageRelativePath = 'assets/topology-original' + $extension
    Copy-Item -LiteralPath $TopologyImagePath -Destination (Join-Path $outDir $imageRelativePath)
    if (-not $TopologyImageSource) { $TopologyImageSource = [IO.Path]::GetFileName($TopologyImagePath) }
    $topologyImageProvenance = [ordered]@{
        kind='provided_existing_image'; source=$TopologyImageSource; exported_file=$imageRelativePath
        source_sha256=(Get-FileHash -LiteralPath $TopologyImagePath -Algorithm SHA256).Hash.ToLowerInvariant()
        dimensions=$imageDimensions; capture_timestamp=$null; pixel_content_modified=$false
        note='An existing image, not a new screenshot captured during this console collection.'
    }
    $topologyDisplayPath=$imageRelativePath
    $topologyDisplayKind='기존 자료의 이미지'
    $topologySection = @"
## 토폴로지 이미지

![기존 자료에서 가져온 토폴로지 이미지]($imageRelativePath)

출처: $TopologyImageSource

제공된 이미지 파일의 바이트를 그대로 보존했습니다. 이번 콘솔 수집 시점에 새로 캡처한 화면은 아니며, 정확한 촬영 시각은 확인되지 않았습니다.

### 보조 자료 — 저장 파일 기반 재구성

![저장된 프로젝트 파일로 재구성한 보조 토폴로지](assets/topology-reconstructed.png)

이 보조 그림은 실제 화면 캡처가 아닙니다. 저장된 장비 배치·연결·주석을 다시 그렸고 아이콘은 단순화했습니다. 원본 주석의 기술 명칭 자체를 실행 결과로 간주하지 않습니다.
"@
}
if($TopologyCaptureMetadataPath) {
    if(-not $TopologyImagePath) { throw 'Capture metadata requires a topology image.' }
    $capture=Get-Content -LiteralPath $TopologyCaptureMetadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if($capture.schema -notin @('gns-proof-window-capture-v1','gns-proof-capture-v2','topolog-capture-v2') -or $capture.capture_status -ne 'captured') { throw 'Capture metadata does not record a successful capture.' }
    if($capture.project_name -ne $projectName) { throw 'Captured project differs from the console collection.' }
    if($capture.image_sha256 -ne $topologyImageProvenance.source_sha256) { throw 'The topology image differs from the recorded capture hash.' }
    if($capture.width -ne $imageDimensions.width -or $capture.height -ne $imageDimensions.height) { throw 'The topology image differs from the recorded capture dimensions.' }
    $capturedAt=([DateTimeOffset]$capture.captured_at).ToString('o')
    Copy-Item -LiteralPath $TopologyCaptureMetadataPath -Destination (Join-Path $outDir 'source\capture.json')
    $topologyImageProvenance.kind='captured_gns3_window'
    $topologyImageProvenance.capture_timestamp=$capturedAt
    $topologyImageProvenance.note='A new GNS3 window capture. Displayed contents remain subject to visual review.'
    $topologyImageProvenance['capture_record']='source/capture.json'
    $topologyDisplayKind='GNS3 창 자동 캡처 · 내용 검토 전'
    $topologySection=@"
## GNS3 창 자동 캡처

![실행 중인 GNS3 창을 자동 캡처한 이미지]($imageRelativePath)

- 촬영 시각: $capturedAt
- 창 제목: $($capture.window_title)
- 캡처 방법: $($capture.method), 창의 클라이언트 영역
- 내용 검토: 사용자 검토 전. 모든 장비와 연결이 화면 안에 보이는지 확인이 필요합니다.
- [자동 캡처 기록](source/capture.json)

기존 기록지의 사진이나 저장 파일 재구성 그림이 아니라 이번 실행에서 새로 저장한 창 이미지입니다. 화면에 보이는 실행 상태와 검증 명령의 실제 결과는 구분합니다.

### 보조 자료 — 저장 파일 기반 재구성

![저장 파일로 재구성한 보조 토폴로지](assets/topology-reconstructed.png)

이 보조 그림은 GNS3 API 조회 결과에서 생성했습니다. 조회와 캡처 시점의 차이가 있을 수 있으며 실제 화면 캡처가 아닙니다.
"@
    if($capture.capture_kind -eq 'gns3_native_topology_screenshot') {
        if($capture.method -ne 'Native') {throw 'The native screenshot capture method is inconsistent.'}
        $nativeRecordPath=Join-Path (Split-Path -Parent $TopologyCaptureMetadataPath) 'native-export.json'
        $nativeRecord=Get-Content -LiteralPath $nativeRecordPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if($nativeRecord.status -ne 'saved') {throw 'The native GNS3 export record did not succeed.'}
        Copy-Item -LiteralPath $nativeRecordPath -Destination (Join-Path $outDir 'source\native-export.json')
        $topologyImageProvenance.kind='gns3_native_topology_screenshot'
        $topologyImageProvenance.note='GNS3 native Screenshot action exported the topology scene. This is not a desktop/window capture or a reconstruction from saved files.'
        $topologyImageProvenance['native_export_record']='source/native-export.json'
        $topologyDisplayKind='GNS3 자체 토폴로지 스크린샷 · 내용 검토 전'
        $topologySection=@"
## GNS3 자체 토폴로지 스크린샷

![GNS3 자체 Screenshot 기능으로 내보낸 토폴로지]($imageRelativePath)

- 생성 시각: $capturedAt
- 프로젝트 창: $($capture.window_title)
- 생성 방법: GNS3의 Take a screenshot 실행 및 PNG 저장 자동화
- 범위: 토폴로지 장면 전체. 창 테두리·메뉴·도구 패널을 찍거나 잘라낸 이미지가 아닙니다.
- 내용 검토: 사용자 검토 전
- [스크린샷 기록](source/capture.json) · [GNS3 자체 내보내기 기록](source/native-export.json)

현재 GNS3가 표시하는 토폴로지 장면을 자체 기능으로 이미지화했습니다. 이미지의 장비 상태 표시와 명령 실행 결과에 따른 기능 판정은 구분합니다.

### 보조 자료 — 저장 파일 기반 재구성

![저장 파일로 재구성한 보조 토폴로지](assets/topology-reconstructed.png)

이 보조 그림은 GNS3 API 조회 결과에서 생성했습니다. 조회와 자체 스크린샷 시점의 차이가 있을 수 있습니다.
"@
    }
}

$sections = [Collections.Generic.List[string]]::new()
$sections.Add(@"
# TopoLog 실습 종합 자료 — $projectName

이 문서는 GNS3 API에서 수집한 프로젝트 정보·저장 설정과 실제 콘솔 수집 결과를 연결한 자료입니다. 최종 포트폴리오 양식이나 실습 성공 판정은 확정하지 않습니다.

## 자료 범위와 출처

- 프로젝트: $projectName / $($collection.project_id)
- 실제 콘솔 수집 장비: $($collection.node_name) / $($collection.node_id)
- 실제 출력 수집 시각: $collectionStartedIso ~ $collectionFinishedIso
- 토폴로지 출처: 현재 GNS3 API의 프로젝트·장비·연결·주석 조회
- 프로젝트 정보 수집 시각: $($snapshot.started_at) ~ $($snapshot.finished_at)
- 프로젝트 ID와 수집 장비 ID 일치 확인. API 조회는 순차적으로 수행하며 원자적 스냅샷이 아닙니다.
- 콘솔 출력의 수집은 완료되었습니다. 실습 동작 판정 상태는 사용자 검토 전입니다.

$topologySection

## 저장된 장비 설정

GNS3 API에서 읽은 startup 설정 ${configCollected}개를 configs/에 담았습니다. 미수집 장비 $($configMissing.Count)개는 아래에 표시합니다. startup 파일은 현재 running-config와 동일하다고 확정하지 않으며 실행 성공의 증거와 구분합니다. API 토폴로지와 수집 기록은 source/에, 설정 출처와 파일 해시는 manifest.json에 기록했습니다.

$configStatusLines

## 실제 검증 명령과 출력

아래 이미지는 실제 수집한 콘솔 텍스트로 생성했습니다. 화면 스크린샷이 아닙니다. logs/의 원본 디코딩 텍스트와 Telnet 수신 바이트를 함께 보존했습니다.
"@)
$index=0
foreach($item in @($collection.commands)) {
    $index++
    $imageName=([IO.Path]::GetFileNameWithoutExtension($item.readable_text_file))+'.png'
    $text=Get-Content -LiteralPath (Join-Path $CollectionDirectory $item.readable_text_file) -Raw -Encoding UTF8
    New-TextImage $text ([string]$item.command) (Join-Path $outDir ('assets\'+$imageName))
    $cliStatus='없음'; if($item.cli_error_detected){$cliStatus='탐지됨'}
    $sections.Add(@"
### $index. $($collection.node_name) — $($item.command)

- 수집 상태: $($item.collection_status)
- CLI 오류 문자열: $cliStatus
- 실습 동작 판정: 사용자 검토 전
- 수집 시각: $(([DateTimeOffset]$item.started_at).ToString('o'))
- [수집 원문](logs/$($item.raw_text_file)) · [읽기용 텍스트](logs/$($item.readable_text_file))

![수집한 콘솔 출력으로 생성한 이미지](assets/$imageName)
"@)
}
$uncollectedNodes=(@($nodes|Where-Object node_id -ne $collection.node_id|ForEach-Object name)) -join ', '
if(-not $uncollectedNodes){$uncollectedNodes='없음'}
$sections.Add(@"
## 미수집·추가 검증 항목

- 이번 묶음에서 실행 출력을 수집하지 않은 장비: $uncollectedNodes
- Ping·traceroute를 실행하지 않았으므로 종단 간 통신 성공은 판정하지 않습니다.
- 장애 전환, 장시간 안정성 및 다른 사용자 환경은 검증하지 않았습니다.
- 실습 목적, 설계 의도, 본인 기여도, 문제 해결 경험은 사용자가 추가합니다.

## 개인 AI에서 문서 편집

ai_prompt.md에 원하는 문서의 목적과 독자를 입력하고 이 자료와 필요한 증거를 함께 첨부합니다. 원본 증거를 보존한 상태에서 별도 문서로 편집합니다.
"@)
[IO.File]::WriteAllText((Join-Path $outDir 'report.md'), ($sections -join "`n`n"), $utf8)
$prompt=@'
첨부한 GNS3 종합 자료를 아래 목적에 맞는 문서로 편집해 주세요.

문서 목적: [기술 보고서 / 취업 포트폴리오 / 학습 정리 / 기타]
독자: [직접 입력]
원하는 분량과 형식: [직접 입력]
강조하고 싶은 경험과 본인 기여도: [확인 가능한 내용만 직접 입력]

규칙:
1. report.md와 원본 로그·설정·토폴로지의 근거를 먼저 확인하세요.
2. 저장 설정과 실행 시점 출력은 구분하세요. 서로 다르면 확인이 필요한 차이로 표시하세요.
3. 설정 존재, 명령 수집 성공, 실제 기능 검증 성공을 구분하세요. CLI 오류가 없다는 것만으로 정상 동작을 확정하지 마세요.
4. 실행하지 않은 테스트, 설계 의도, 본인 기여도, 장애 해결 경험, 성능 향상을 만들어내지 마세요.
5. 실패·미검증·증거 부족 상태를 유지하고, 필요한 추가 질문을 정리하세요.
6. 이미지별 출처를 확인하고 기존 기록지의 이미지, 새 화면 캡처, 토폴로지 재구성 이미지, 로그로 생성한 이미지를 구분하세요. 촬영 시각을 추측하지 마세요.
7. 목적에 맞게 구성과 문장을 바꾸되 증거 연결과 이미지 상대경로를 유지하세요.
8. 최종 문서와 추가 확인이 필요한 사항을 구분해 제시하세요.
'@
[IO.File]::WriteAllText((Join-Path $outDir 'ai_prompt.md'),$prompt,$utf8)

# Self-contained offline report preview. No network service or final app
# architecture is implied by this HTML document.
function Encode-Html([string]$Text) { return [Net.WebUtility]::HtmlEncode($Text) }
$commandCards=[Collections.Generic.List[string]]::new()
foreach($item in @($collection.commands)) {
    $imageName=([IO.Path]::GetFileNameWithoutExtension($item.readable_text_file))+'.png'
    $body=Get-Content -LiteralPath (Join-Path $CollectionDirectory $item.readable_text_file) -Raw -Encoding UTF8
    $commandTitle=Encode-Html ([string]$item.command)
    $commandCards.Add(@"
<article class="card"><h3>$commandTitle</h3><p class="muted">장비 $(Encode-Html $collection.node_name) · 출력 수집 완료 · 동작 판정 검토 전</p><img loading="lazy" src="assets/$imageName" alt="$commandTitle 출력 이미지"><details><summary>수집 텍스트 보기</summary><pre>$(Encode-Html $body)</pre></details><p><a href="logs/$($item.raw_text_file)">원본 디코딩 로그</a> · <a href="logs/$($item.readable_text_file)">읽기용 텍스트</a></p></article>
"@)
}
$html=@"
<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TopoLog — $(Encode-Html $projectName)</title><style>
:root{color-scheme:light;font-family:system-ui,'Malgun Gothic',sans-serif;color:#172b40;background:#f2f5f8}*{box-sizing:border-box}body{margin:0}header{background:#17324e;color:white;padding:32px max(24px,calc((100vw - 1120px)/2))}header h1{margin:0 0 8px;font-size:30px}header p{color:#d3e1ef}main{max-width:1168px;margin:28px auto;padding:0 24px 40px}nav{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}nav a{background:white;padding:10px 16px;border:1px solid #d2dde8;border-radius:8px;text-decoration:none}.card{background:white;border:1px solid #dce4ec;border-radius:12px;padding:24px;margin-bottom:24px}h2{font-size:23px}h3{font-size:19px}a{color:#205c92}img{max-width:100%;height:auto;border:1px solid #e4e9ef;border-radius:6px}.muted{color:#52677b;font-size:14px;line-height:1.6}pre{white-space:pre;overflow:auto;background:#f4f6f8;padding:18px;font-size:13px;line-height:1.6}details{margin:16px 0}summary{cursor:pointer}li{margin:8px 0;line-height:1.6}.badge{display:inline-block;background:#e7eff7;color:#214b72;padding:6px 10px;border-radius:6px;font-size:13px}footer{color:#52677b;font-size:13px;margin:24px 0}@media(max-width:600px){header{padding:24px}main{padding:0 14px}.card{padding:16px}header h1{font-size:25px}}
</style></head><body><header><h1>TopoLog</h1><p>GNS3 실습의 구성과 검증 증거를 한곳에</p><strong>$(Encode-Html $projectName) · $(Encode-Html $collection.node_name)</strong></header><main><nav><a href="#topology">토폴로지</a><a href="#commands">검증 출력</a><a href="report.md">Markdown 보고서</a><a href="ai_prompt.md">AI 편집 프롬프트</a><a href="manifest.json">파일 출처·해시</a></nav>
<section class="card"><h2>수집 요약</h2><p>검증 명령 $(@($collection.commands).Count)개 · 장비 $(Encode-Html $collection.node_name) · $collectionStartedIso</p><span class="badge">수집 완료 / 실습 동작 판정은 별도</span><p class="muted">원본 로그, 저장 설정, 캡처·생성 이미지의 출처를 구분해 보존합니다. 이 문서는 수집 결과의 오프라인 미리보기입니다.</p></section>
<section class="card" id="topology"><h2>토폴로지</h2><p class="muted">$(Encode-Html $topologyDisplayKind)</p><img src="$topologyDisplayPath" alt="토폴로지 증거"><details><summary>저장 파일 기반 보조 그림 보기</summary><img src="assets/topology-reconstructed.png" alt="저장 파일로 재구성한 토폴로지"></details></section>
<section id="commands"><h2>검증 명령과 실제 출력</h2>$($commandCards -join "`n")</section>
<section class="card"><h2>설정 수집</h2><p>저장된 startup 파일 ${configCollected}개 · 미수집 $($configMissing.Count)개</p><pre>$(Encode-Html $configStatusLines)</pre></section>
<section class="card"><h2>아직 확인하지 않은 것</h2><ul><li>이번 묶음의 실제 출력 수집 대상은 $(Encode-Html $collection.node_name)입니다.</li><li>Ping·traceroute와 장애 전환 시험은 수행하지 않았습니다.</li><li>저장된 startup 파일과 현재 running-config는 다를 수 있습니다.</li><li>화면 자동 캡처가 포함된 경우에도 전체 토폴로지가 보이는지 이미지 검토가 필요합니다.</li></ul></section>
<section class="card"><h2>다음 활용</h2><p>개인 AI 계정에 종합 자료와 <a href="ai_prompt.md">편집 프롬프트</a>를 전달해 원하는 목적의 문서로 편집할 수 있습니다.</p></section><footer>TopoLog · 증거 수집과 문서 편집을 구분합니다.</footer></main></body></html>
"@
[IO.File]::WriteAllText((Join-Path $outDir 'index.html'),$html,$utf8)
$fileManifest=@(Get-ChildItem -LiteralPath $outDir -Recurse -File | ForEach-Object {
    [pscustomobject]@{ path=$_.FullName.Substring($outDir.Length+1).Replace('\','/'); bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
$manifest=[ordered]@{ project_name=$projectName; generated_at=[DateTimeOffset]::Now.ToString('o'); project_source='GNS3 controller API v2 GET'; project_snapshot_record='source/project-snapshot.json'; source_topology_file='source/topology.json'; source_topology_sha256=$snapshot.topology_sha256; topology_image=$topologyImageProvenance; collection=$collection; config_sources=$configSources; files=$fileManifest; note='The manifest does not include its own hash. API data is a sequential snapshot. Startup configuration files are not running configurations. A supplied image has separate provenance.' }
[IO.File]::WriteAllText((Join-Path $outDir 'manifest.json'),($manifest|ConvertTo-Json -Depth 10),$utf8)
$zipPath=$outDir+'.zip'
[IO.Compression.ZipFile]::CreateFromDirectory($outDir,$zipPath,[IO.Compression.CompressionLevel]::Optimal,$false)
Write-Host ('EXPORT_DIRECTORY: '+$outDir)
Write-Host ('EXPORT_ZIP: '+$zipPath)
if($ResultPath) {
    $result=[ordered]@{ export_directory=$outDir; export_zip=$zipPath; report_file=(Join-Path $outDir 'report.md'); html_preview=(Join-Path $outDir 'index.html') }
    $resultFile=[IO.Path]::GetFullPath($ResultPath)
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($resultFile))|Out-Null
    [IO.File]::WriteAllText($resultFile,($result|ConvertTo-Json),$utf8)
}
