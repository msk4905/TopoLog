using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

public class TopoLogWindowInfo {
    public long Handle;
    public string Title;
    public int ProcessId;
    public string ProcessName;
    public bool Minimized;
}
public class TopoLogDialogInfo {
    public long Handle;
    public string Title;
    public int ProcessId;
    public long OwnerHandle;
    public string ClassName;
}

// Native capture of one explicitly matched GNS3 window, never the entire desktop.
// https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-printwindow
public static class TopoLogWindowCapture {
    [StructLayout(LayoutKind.Sequential)] private struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] private struct Point { public int X, Y; }
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool ShowWindowAsync(IntPtr hwnd, int command);
    [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr hwnd, uint command);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd,StringBuilder text,int size);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr hwnd,EnumWindowsProc callback,IntPtr parameter);
    [DllImport("user32.dll")] private static extern int GetDlgCtrlID(IntPtr hwnd);
    [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="SendMessageTimeoutW")]
    private static extern IntPtr SendTextTimeout(IntPtr hwnd,uint message,IntPtr wParam,string text,uint flags,uint timeout,out IntPtr result);
    [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="SendMessageTimeoutW")]
    private static extern IntPtr ReadTextTimeout(IntPtr hwnd,uint message,IntPtr wParam,StringBuilder text,uint flags,uint timeout,out IntPtr result);
    [DllImport("user32.dll")] private static extern bool PostMessage(IntPtr hwnd,uint message,IntPtr wParam,IntPtr lParam);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int size);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
    [DllImport("user32.dll")] private static extern bool GetClientRect(IntPtr hwnd, out Rect rect);
    [DllImport("user32.dll")] private static extern bool ClientToScreen(IntPtr hwnd, ref Point point);
    [DllImport("user32.dll")] private static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);

    public static bool TitleMatches(string title, string projectName) {
        return title != null && title.IndexOf("GNS3", StringComparison.OrdinalIgnoreCase) >= 0 &&
            Regex.IsMatch(title, @"(?<![A-Za-z0-9])" + Regex.Escape(projectName) + @"(?![A-Za-z0-9])", RegexOptions.IgnoreCase);
    }
    public static bool IsScreenshotDialog(string title,int processId,long owner,long mainWindow,int gns3ProcessId) {
        return String.Equals(title,"Screenshot",StringComparison.OrdinalIgnoreCase) &&
            (processId==gns3ProcessId || owner==mainWindow);
    }
    public static TopoLogDialogInfo[] FindScreenshotDialogs(long mainWindow,int gns3ProcessId) {
        var dialogs=new List<TopoLogDialogInfo>();
        EnumWindows(delegate(IntPtr hwnd,IntPtr unused) {
            if(hwnd.ToInt64()==mainWindow || !IsWindowVisible(hwnd)) return true;
            var text=new StringBuilder(2048);GetWindowText(hwnd,text,text.Capacity);
            if(!String.Equals(text.ToString(),"Screenshot",StringComparison.OrdinalIgnoreCase)) return true;
            uint pid;GetWindowThreadProcessId(hwnd,out pid);
            IntPtr owner=GetWindow(hwnd,4),ancestor=owner;
            bool owned=false;
            for(int i=0;i<16 && ancestor!=IntPtr.Zero;i++) {
                if(ancestor.ToInt64()==mainWindow){owned=true;break;}
                ancestor=GetWindow(ancestor,4);
            }
            if(!IsScreenshotDialog(text.ToString(),(int)pid,owned ? mainWindow : owner.ToInt64(),mainWindow,gns3ProcessId)) return true;
            var className=new StringBuilder(256);GetClassName(hwnd,className,className.Capacity);
            dialogs.Add(new TopoLogDialogInfo {Handle=hwnd.ToInt64(),Title=text.ToString(),ProcessId=(int)pid,OwnerHandle=owner.ToInt64(),ClassName=className.ToString()});
            return true;
        },IntPtr.Zero);
        return dialogs.ToArray();
    }
    public static long FindDialogControl(long dialog,int id,string expectedClass) {
        var matches=new List<long>();
        EnumChildWindows(new IntPtr(dialog),delegate(IntPtr hwnd,IntPtr unused) {
            if(GetDlgCtrlID(hwnd)!=id) return true;
            var className=new StringBuilder(256);GetClassName(hwnd,className,className.Capacity);
            if(String.Equals(className.ToString(),expectedClass,StringComparison.OrdinalIgnoreCase)) matches.Add(hwnd.ToInt64());
            return true;
        },IntPtr.Zero);
        if(matches.Count!=1) throw new InvalidOperationException("Could not uniquely locate dialog control "+id+" ("+expectedClass+").");
        return matches[0];
    }
    public static void SetDialogFilename(long edit,string filename) {
        IntPtr result;
        if(SendTextTimeout(new IntPtr(edit),12,IntPtr.Zero,filename,2,2500,out result)==IntPtr.Zero || result==IntPtr.Zero)
            throw new InvalidOperationException("The screenshot filename field did not accept the destination.");
        var value=new StringBuilder(32768);
        if(ReadTextTimeout(new IntPtr(edit),13,new IntPtr(value.Capacity),value,2,2500,out result)==IntPtr.Zero || value.ToString()!=filename)
            throw new InvalidOperationException("The screenshot filename could not be verified.");
    }
    public static void ClickDialogButton(long handle) {
        if(!PostMessage(new IntPtr(handle),245,IntPtr.Zero,IntPtr.Zero))
            throw new InvalidOperationException("Could not click the screenshot dialog button.");
    }
    public static TopoLogWindowInfo[] List() {
        var windows = new List<TopoLogWindowInfo>();
        EnumWindows(delegate(IntPtr hwnd, IntPtr unused) {
            if (!IsWindowVisible(hwnd)) return true;
            var text = new StringBuilder(2048);
            GetWindowText(hwnd, text, text.Capacity);
            string title = text.ToString();
            if (title.IndexOf("GNS3", StringComparison.OrdinalIgnoreCase) < 0) return true;
            uint pid;
            GetWindowThreadProcessId(hwnd, out pid);
            try {
                var process = Process.GetProcessById((int)pid);
                string name = process.ProcessName;
                // A browser, console or editor merely mentioning GNS3 is not a target.
                if (!(name.StartsWith("gns3",StringComparison.OrdinalIgnoreCase) ||
                      name.Equals("python",StringComparison.OrdinalIgnoreCase) ||
                      name.Equals("pythonw",StringComparison.OrdinalIgnoreCase))) return true;
                windows.Add(new TopoLogWindowInfo {Handle=hwnd.ToInt64(),Title=title,ProcessId=(int)pid,ProcessName=name,Minimized=IsIconic(hwnd)});
            } catch (ArgumentException) { }
            catch (System.ComponentModel.Win32Exception) { }
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }
    public static bool HasVisualContent(Bitmap bitmap) {
        // Detect a blank capture, not whether the displayed topology is correct.
        var colors = new HashSet<int>();
        int min=255,max=0;
        int stepX=Math.Max(1,bitmap.Width/100),stepY=Math.Max(1,bitmap.Height/80);
        for(int y=0;y<bitmap.Height;y+=stepY) for(int x=0;x<bitmap.Width;x+=stepX) {
            Color color=bitmap.GetPixel(x,y);
            int l=(color.R+color.G+color.B)/3;
            min=Math.Min(min,l);max=Math.Max(max,l);
            colors.Add(color.ToArgb());
        }
        return colors.Count>8 && max-min>20;
    }
    public static bool PrepareWindow(long handle, bool foreground) {
        IntPtr hwnd=new IntPtr(handle);
        bool restored=false;
        if(IsIconic(hwnd)) {
            ShowWindowAsync(hwnd,9);
            var clock=Stopwatch.StartNew();
            while(IsIconic(hwnd) && clock.ElapsedMilliseconds<5000) Thread.Sleep(50);
            if(IsIconic(hwnd)) throw new InvalidOperationException("The GNS3 window could not be restored within five seconds.");
            restored=true;
            Thread.Sleep(700);
        }
        if(foreground) SetForegroundWindow(hwnd);
        return restored;
    }
    public static int[] Capture(long handle, string destination, string method) {
        IntPtr hwnd=new IntPtr(handle),previous=IntPtr.Zero;
        try { previous=SetThreadDpiAwarenessContext(new IntPtr(-4)); }
        catch(EntryPointNotFoundException) { }
        try {
            bool restored=PrepareWindow(handle,false);
            Rect rect;
            if(!GetClientRect(hwnd,out rect)) throw new InvalidOperationException("Cannot read the GNS3 client rectangle.");
            int width=rect.Right-rect.Left,height=rect.Bottom-rect.Top;
            if(width<300 || height<200 || width>16000 || height>16000) throw new InvalidOperationException("Unexpected GNS3 client dimensions.");
            using(var bitmap=new Bitmap(width,height,PixelFormat.Format24bppRgb)) {
                using(var graphics=Graphics.FromImage(bitmap)) {
                    graphics.Clear(Color.White);
                    if(method=="PrintWindow") {
                        IntPtr hdc=graphics.GetHdc();
                        bool ok;
                        try { ok=PrintWindow(hwnd,hdc,1); } finally { graphics.ReleaseHdc(hdc); }
                        if(!ok) throw new InvalidOperationException("PrintWindow did not succeed. Try -Method Screen.");
                    } else if(method=="Screen") {
                        // This explicit method requires the matching GNS3 window in front.
                        SetForegroundWindow(hwnd);
                        Thread.Sleep(600);
                        if(GetForegroundWindow()!=hwnd) throw new InvalidOperationException("Could not bring the matched GNS3 window to the foreground.");
                        Point origin=new Point();
                        if(!ClientToScreen(hwnd,ref origin)) throw new InvalidOperationException("Cannot locate the GNS3 client area.");
                        graphics.CopyFromScreen(origin.X,origin.Y,0,0,new Size(width,height),CopyPixelOperation.SourceCopy);
                    } else throw new ArgumentException("Unknown capture method.");
                }
                if(!HasVisualContent(bitmap)) throw new InvalidOperationException("Capture appears blank. No successful image was recorded. Try -Method Screen.");
                bitmap.Save(destination,ImageFormat.Png);
                return new int[] {width,height,restored ? 1 : 0};
            }
        } finally { if(previous!=IntPtr.Zero) SetThreadDpiAwarenessContext(previous); }
    }
}
