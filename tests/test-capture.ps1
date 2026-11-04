#requires -Version 5.1
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$root=Split-Path -Parent $PSScriptRoot
$references=@('System','System.Core','System.Drawing')
if($PSVersionTable.PSEdition -eq 'Core') {
    $references=@(Get-ChildItem -LiteralPath (Join-Path $PSHOME 'ref') -Filter '*.dll' | ForEach-Object FullName)+
        @([Drawing.Bitmap].Assembly.Location)+
        @(Get-ChildItem -LiteralPath $PSHOME -Filter 'System.Private.Windows*.dll' | ForEach-Object FullName)
}
Add-Type -Path (Join-Path $root 'tools\TopoLogWindowCapture.cs') -ReferencedAssemblies ($references | Select-Object -Unique)
if(-not [TopoLogWindowCapture]::TitleMatches('20260827-1 - GNS3','20260827-1')){throw 'Expected title failed'}
if([TopoLogWindowCapture]::TitleMatches('20260827-10 - GNS3','20260827-1')){throw 'Project prefix collision accepted'}
if([TopoLogWindowCapture]::TitleMatches('20260827-1-copy - GNS3','20260827-1')){throw 'Project suffix collision accepted'}
if([TopoLogWindowCapture]::TitleMatches('20260827-1 - PowerShell','20260827-1')){throw 'Non-GNS3 title accepted'}
if(-not [TopoLogWindowCapture]::IsScreenshotDialog('Screenshot',8532,67228,67228,8532)){throw 'Owned GNS3 save dialog rejected'}
if(-not [TopoLogWindowCapture]::IsScreenshotDialog('Screenshot',9000,67228,67228,8532)){throw 'Owned proxy dialog rejected'}
if([TopoLogWindowCapture]::IsScreenshotDialog('Screenshot',9000,33333,67228,8532)){throw 'Unrelated application dialog accepted'}
if([TopoLogWindowCapture]::IsScreenshotDialog('Save As',8532,67228,67228,8532)){throw 'Unrelated GNS3 dialog accepted'}
$image=[Drawing.Bitmap]::new(400,300)
$graphics=[Drawing.Graphics]::FromImage($image)
try {
    $graphics.Clear([Drawing.Color]::White)
    $graphics.Flush()
    if([TopoLogWindowCapture]::HasVisualContent($image)){throw 'Blank capture accepted'}
    for($i=0;$i -lt 30;$i++) {
        $brush=[Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(($i*8),100,200))
        try{$graphics.FillRectangle($brush,($i*12),0,12,300)}finally{$brush.Dispose()}
    }
    $graphics.Flush()
    if(-not [TopoLogWindowCapture]::HasVisualContent($image)){throw 'Nonblank image rejected'}
} finally {$graphics.Dispose();$image.Dispose()}
if(-not [TopoLogWindowCapture]::IsFilenameEdit(1001,@(1001))){throw 'Save dialog filename edit rejected'}
if(-not [TopoLogWindowCapture]::IsFilenameEdit(1148,@(1148,1148))){throw 'Open dialog filename edit rejected'}
if(-not [TopoLogWindowCapture]::IsFilenameEdit(0,@(0,1148))){throw 'Edit under cmb13 rejected'}
if([TopoLogWindowCapture]::IsFilenameEdit(41477,@(41477,41477))){throw 'Address bar edit accepted'}
if([TopoLogWindowCapture]::ChooseFilenameCandidate(@([long]7),@($false)) -ne 7){throw 'Single filename candidate rejected'}
if([TopoLogWindowCapture]::ChooseFilenameCandidate(@([long]7,[long]8),@($false,$true)) -ne 8){throw 'Visible filename candidate not preferred'}
if([TopoLogWindowCapture]::ChooseFilenameCandidate(@([long]7,[long]8),@($true,$true)) -ne 0){throw 'Ambiguous visible filename candidates accepted'}
if([TopoLogWindowCapture]::ChooseFilenameCandidate(@([long]7,[long]8),@($false,$false)) -ne 0){throw 'Ambiguous hidden filename candidates accepted'}
Write-Output ('Capture logic checks: PASS on PowerShell '+$PSVersionTable.PSVersion.ToString())
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class TopoLogHiddenDialogTest {
    [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
    private static extern IntPtr CreateWindowEx(uint extendedStyle,string className,string title,uint style,int x,int y,int width,int height,IntPtr parent,IntPtr menu,IntPtr instance,IntPtr parameter);
    [DllImport("user32.dll")] public static extern bool DestroyWindow(IntPtr hwnd);
    public static IntPtr Create() {
        IntPtr root=CreateWindowEx(0,"STATIC","Hidden TopoLog control test",0x80000000U,0,0,400,200,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero);
        if(root==IntPtr.Zero) throw new InvalidOperationException("Could not create the hidden test window.");
        IntPtr edit=CreateWindowEx(0,"EDIT","",0x40000000U,0,0,250,20,root,new IntPtr(1001),IntPtr.Zero,IntPtr.Zero);
        IntPtr save=CreateWindowEx(0,"BUTTON","Save",0x40000000U,0,30,80,20,root,new IntPtr(1),IntPtr.Zero,IntPtr.Zero);
        if(edit==IntPtr.Zero || save==IntPtr.Zero) {DestroyWindow(root);throw new InvalidOperationException("Could not create hidden test controls.");}
        return root;
    }
    // Mirrors the Vista+ Open dialog: cmb13(1148) > ComboBox(1148) > Edit(1148), plus an address-bar edit (0xA205).
    public static IntPtr CreateOpenLayout() {
        IntPtr root=CreateWindowEx(0,"STATIC","Hidden TopoLog open test",0x80000000U,0,0,400,200,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero);
        if(root==IntPtr.Zero) throw new InvalidOperationException("Could not create the hidden open test window.");
        IntPtr outer=CreateWindowEx(0,"STATIC","",0x40000000U,0,0,250,20,root,new IntPtr(1148),IntPtr.Zero,IntPtr.Zero);
        IntPtr inner=CreateWindowEx(0,"STATIC","",0x40000000U,0,0,250,20,outer,new IntPtr(1148),IntPtr.Zero,IntPtr.Zero);
        IntPtr edit=CreateWindowEx(0,"EDIT","",0x40000000U,0,0,250,20,inner,new IntPtr(1148),IntPtr.Zero,IntPtr.Zero);
        IntPtr address=CreateWindowEx(0,"STATIC","",0x40000000U,0,40,250,20,root,new IntPtr(41477),IntPtr.Zero,IntPtr.Zero);
        IntPtr addressEdit=CreateWindowEx(0,"EDIT","",0x40000000U,0,0,250,20,address,new IntPtr(41477),IntPtr.Zero,IntPtr.Zero);
        IntPtr open=CreateWindowEx(0,"BUTTON","Open",0x40000000U,0,80,80,20,root,new IntPtr(1),IntPtr.Zero,IntPtr.Zero);
        if(edit==IntPtr.Zero || addressEdit==IntPtr.Zero || open==IntPtr.Zero) {DestroyWindow(root);throw new InvalidOperationException("Could not create hidden open test controls.");}
        return root;
    }
}
'@
$hidden=[TopoLogHiddenDialogTest]::Create()
try {
    $edit=[TopoLogWindowCapture]::FindDialogControl($hidden.ToInt64(),1001,'Edit')
    $save=[TopoLogWindowCapture]::FindDialogControl($hidden.ToInt64(),1,'Button')
    if($save -eq 0){throw 'Hidden save button was not found'}
    $testFilename='C:\test folder\'+[char]0xAC80+[char]0xC99D+'\topology.png'
    [TopoLogWindowCapture]::SetDialogFilename($edit,$testFilename)
    if([TopoLogWindowCapture]::FindFilenameControl($hidden.ToInt64()) -ne $edit){throw 'Save layout filename edit not selected'}
    Write-Output 'Native control check: PASS (hidden filename edit, Unicode path, verified text, Save button ID)'
} finally {[void][TopoLogHiddenDialogTest]::DestroyWindow($hidden)}
$openLayout=[TopoLogHiddenDialogTest]::CreateOpenLayout()
try {
    $openEdit=[TopoLogWindowCapture]::FindFilenameControl($openLayout.ToInt64())
    if([TopoLogWindowCapture]::ControlId($openEdit) -ne 1148){throw 'Open layout selected the wrong edit'}
    [TopoLogWindowCapture]::SetDialogFilename($openEdit,'C:\test folder\lab.gns3')
    if([TopoLogWindowCapture]::FindDialogControl($openLayout.ToInt64(),1,'Button') -eq 0){throw 'Open button not found'}
    if(@([TopoLogWindowCapture]::DescribeDialogControls($openLayout.ToInt64())).Count -lt 3){throw 'Dialog control diagnostic is incomplete'}
    Write-Output 'Native open-layout check: PASS (nested 1148 edit selected, address edit excluded, diagnostics listed)'
} finally {[void][TopoLogHiddenDialogTest]::DestroyWindow($openLayout)}
Write-Output 'Actual GNS3 window capture remains a separate interactive test.'
