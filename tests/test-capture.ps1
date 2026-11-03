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
}
'@
$hidden=[TopoLogHiddenDialogTest]::Create()
try {
    $edit=[TopoLogWindowCapture]::FindDialogControl($hidden.ToInt64(),1001,'Edit')
    $save=[TopoLogWindowCapture]::FindDialogControl($hidden.ToInt64(),1,'Button')
    if($save -eq 0){throw 'Hidden save button was not found'}
    $testFilename='C:\test folder\'+[char]0xAC80+[char]0xC99D+'\topology.png'
    [TopoLogWindowCapture]::SetDialogFilename($edit,$testFilename)
    Write-Output 'Native control check: PASS (hidden filename edit, Unicode path, verified text, Save button ID)'
} finally {[void][TopoLogHiddenDialogTest]::DestroyWindow($hidden)}
Write-Output 'Actual GNS3 window capture remains a separate interactive test.'
