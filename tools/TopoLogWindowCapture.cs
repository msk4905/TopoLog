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
    [DllImport("user32.dll")] private static extern bool BringWindowToTop(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(uint source,uint target,bool attach);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    public static bool BringForeground(long handle) {
        IntPtr target=new IntPtr(handle);
        if(!IsWindow(target)) return false;
        if(GetForegroundWindow()==target) return true;
        IntPtr before=GetForegroundWindow();
        SetForegroundWindow(target);
        if(GetForegroundWindow()==target) return true;
        // Do not override a different window chosen during the first attempt.
        if(GetForegroundWindow()!=before) return false;
        uint pid;
        uint foregroundThread=GetWindowThreadProcessId(before,out pid);
        uint ownThread=GetCurrentThreadId();
        bool attached=foregroundThread!=0 && ownThread!=foregroundThread && AttachThreadInput(ownThread,foregroundThread,true);
        try {
            if(GetForegroundWindow()!=before) return false;
            BringWindowToTop(target);
            SetForegroundWindow(target);
        } finally {
            if(attached) AttachThreadInput(ownThread,foregroundThread,false);
        }
        return GetForegroundWindow()==target;
    }
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);

    [DllImport("user32.dll")] private static extern bool IsWindow(IntPtr hwnd);
    public static string WindowTitle(long handle) {
        var text=new StringBuilder(2048);GetWindowText(new IntPtr(handle),text,text.Capacity);return text.ToString();
    }
    public static long ForegroundHandle() { return GetForegroundWindow().ToInt64(); }
    public static int WindowProcessId(long handle) {
        if(handle==0) return 0;
        uint pid;GetWindowThreadProcessId(new IntPtr(handle),out pid);return (int)pid;
    }
    public static string ProcessNameOf(int pid) {
        if(pid<=0) return "";
        try { return Process.GetProcessById(pid).ProcessName; } catch(ArgumentException) { return ""; }
        catch(InvalidOperationException) { return ""; }
    }
    // Visible top-level windows of one process (diagnostics for a stalled project open).
    public static TopoLogDialogInfo[] ListProcessWindows(int processId) {
        var windows=new List<TopoLogDialogInfo>();
        EnumWindows(delegate(IntPtr hwnd,IntPtr unused) {
            if(!IsWindowVisible(hwnd)) return true;
            uint pid;GetWindowThreadProcessId(hwnd,out pid);
            if((int)pid!=processId) return true;
            var text=new StringBuilder(2048);GetWindowText(hwnd,text,text.Capacity);
            var className=new StringBuilder(256);GetClassName(hwnd,className,className.Capacity);
            windows.Add(new TopoLogDialogInfo {Handle=hwnd.ToInt64(),Title=text.ToString(),ProcessId=(int)pid,OwnerHandle=GetWindow(hwnd,4).ToInt64(),ClassName=className.ToString()});
            return true;
        },IntPtr.Zero);
        return windows.ToArray();
    }
    // Put back the window the user had in front before TopoLog raised GNS3.
    // Only when GNS3 (the window TopoLog raised) is still in front; a window the
    // user switched to meanwhile is left alone. Minimizes GNS3 again only if
    // TopoLog restored it from the minimized state. Windows may refuse; that is reported.
    public static string RestoreForeground(long previous,int gns3ProcessId,long gns3Window,bool minimizeGns3) {
        int previousPid=WindowProcessId(previous);
        IntPtr current=GetForegroundWindow();
        int currentPid=WindowProcessId(current.ToInt64());
        string currentName=ProcessNameOf(currentPid).ToLowerInvariant();
        bool gns3InFront=current==IntPtr.Zero || currentPid==gns3ProcessId ||
            currentName=="vmware" || currentName=="vmplayer" || currentName=="vmware-vmx" ||
            currentName=="virtualbox" || currentName=="virtualboxvm";
        if(minimizeGns3 && gns3Window!=0 && IsWindow(new IntPtr(gns3Window)) && !IsIconic(new IntPtr(gns3Window)) && (gns3InFront || current.ToInt64()!=gns3Window))
            ShowWindowAsync(new IntPtr(gns3Window),7);
        if(previous==0) return "no_previous";
        if(previousPid==gns3ProcessId) return "previous_was_gns3";
        if(!IsWindow(new IntPtr(previous))) return "previous_closed";
        if(current.ToInt64()==previous) return "unchanged";
        if(!gns3InFront) return "user_switched";
        if(IsIconic(new IntPtr(previous))) return "previous_minimized";
        BringForeground(previous);
        Thread.Sleep(150);
        return GetForegroundWindow().ToInt64()==previous ? "restored" : "os_refused";
    }
    public static bool TitleMatches(string title, string projectName) {
        return title != null && String.Equals(title.Trim(), projectName + " - GNS3", StringComparison.OrdinalIgnoreCase);
    }
    public static bool IsScreenshotDialog(string title,int processId,long owner,long mainWindow,int gns3ProcessId) {
        return String.Equals(title,"Screenshot",StringComparison.OrdinalIgnoreCase) &&
            (processId==gns3ProcessId || owner==mainWindow);
    }
    public static TopoLogDialogInfo[] FindScreenshotDialogs(long mainWindow,int gns3ProcessId) {
        return FindOwnedDialogs("Screenshot",mainWindow,gns3ProcessId);
    }
    public static TopoLogDialogInfo[] FindProjectOpenDialogs(long mainWindow,int gns3ProcessId) {
        return FindOwnedDialogs("Open project",mainWindow,gns3ProcessId);
    }
    // GNS3's startup chooser (title "Project") is modal and blocks the Open project action.
    public static TopoLogDialogInfo[] FindStartupProjectDialogs(long mainWindow,int gns3ProcessId) {
        return FindOwnedDialogs("Project",mainWindow,gns3ProcessId);
    }
    // WM_CLOSE is the same as the dialog's window close button (Qt reject); nothing is saved.
    public static void CloseWindow(long handle) {
        if(!PostMessage(new IntPtr(handle),16,IntPtr.Zero,IntPtr.Zero))
            throw new InvalidOperationException("Could not close the GNS3 startup dialog.");
    }
    private static TopoLogDialogInfo[] FindOwnedDialogs(string expectedTitle,long mainWindow,int gns3ProcessId) {
        var dialogs=new List<TopoLogDialogInfo>();
        EnumWindows(delegate(IntPtr hwnd,IntPtr unused) {
            if(hwnd.ToInt64()==mainWindow || !IsWindowVisible(hwnd)) return true;
            var text=new StringBuilder(2048);GetWindowText(hwnd,text,text.Capacity);
            if(!String.Equals(text.ToString(),expectedTitle,StringComparison.OrdinalIgnoreCase)) return true;
            uint pid;GetWindowThreadProcessId(hwnd,out pid);
            IntPtr owner=GetWindow(hwnd,4),ancestor=owner;
            bool owned=false;
            for(int i=0;i<16 && ancestor!=IntPtr.Zero;i++) {
                if(ancestor.ToInt64()==mainWindow){owned=true;break;}
                ancestor=GetWindow(ancestor,4);
            }
            if((int)pid!=gns3ProcessId && !owned) return true;
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
    [DllImport("user32.dll")] private static extern IntPtr GetParent(IntPtr hwnd);
    public static long FindFilenameControl(long dialog) {
        // Vista+ Open dialogs put the filename Edit under cmb13 (1148:
        // ComboBoxEx32 > ComboBox > Edit), Save dialogs use 1001. Unrelated
        // address/search fields have other IDs and are never selected.
        var handles=new List<long>();var visible=new List<bool>();
        EnumChildWindows(new IntPtr(dialog),delegate(IntPtr hwnd,IntPtr unused) {
            var name=new StringBuilder(256);GetClassName(hwnd,name,name.Capacity);
            if(!String.Equals(name.ToString(),"Edit",StringComparison.OrdinalIgnoreCase)) return true;
            var ancestors=new List<int>();
            IntPtr parent=GetParent(hwnd);
            for(int i=0;i<4 && parent!=IntPtr.Zero && parent.ToInt64()!=dialog;i++) {
                ancestors.Add(GetDlgCtrlID(parent));
                parent=GetParent(parent);
            }
            if(IsFilenameEdit(GetDlgCtrlID(hwnd),ancestors.ToArray())) {
                handles.Add(hwnd.ToInt64());visible.Add(IsWindowVisible(hwnd));
            }
            return true;
        },IntPtr.Zero);
        long chosen=ChooseFilenameCandidate(handles.ToArray(),visible.ToArray());
        if(chosen==0) throw new InvalidOperationException("Could not uniquely identify the filename edit control (1001/1148); candidates="+handles.Count+".");
        return chosen;
    }
    public static bool IsFilenameEdit(int id,int[] ancestorIds) {
        if(id==1001 || id==1148) return true;
        if(ancestorIds==null) return false;
        foreach(int ancestor in ancestorIds) if(ancestor==1148) return true;
        return false;
    }
    // Returns 0 unless exactly one candidate exists, or exactly one of several is visible.
    public static long ChooseFilenameCandidate(long[] handles,bool[] visible) {
        if(handles==null || visible==null || handles.Length!=visible.Length || handles.Length==0) return 0;
        if(handles.Length==1) return handles[0];
        long only=0;int count=0;
        for(int i=0;i<handles.Length;i++) if(visible[i]){only=handles[i];count++;}
        return count==1 ? only : 0;
    }
    public static int ControlId(long handle) { return GetDlgCtrlID(new IntPtr(handle)); }
    // Bounded diagnostic of a dialog's input controls; no text content is read.
    public static string[] DescribeDialogControls(long dialog) {
        var rows=new List<string>();
        EnumChildWindows(new IntPtr(dialog),delegate(IntPtr hwnd,IntPtr unused) {
            if(rows.Count>=80) return false;
            var name=new StringBuilder(256);GetClassName(hwnd,name,name.Capacity);
            string cls=name.ToString();
            if(cls!="Edit" && cls!="ComboBox" && cls!="ComboBoxEx32" && cls!="Button" && cls!="DirectUIHWND") return true;
            IntPtr parent=GetParent(hwnd);
            var parentName=new StringBuilder(256);
            if(parent!=IntPtr.Zero) GetClassName(parent,parentName,parentName.Capacity);
            rows.Add(cls+"|id="+GetDlgCtrlID(hwnd)+"|visible="+(IsWindowVisible(hwnd)?1:0)+"|parent="+parentName+":"+(parent==IntPtr.Zero?0:GetDlgCtrlID(parent)));
            return true;
        },IntPtr.Zero);
        return rows.ToArray();
    }
    public static string ReadDialogFilename(long edit) {
        IntPtr result;var value=new StringBuilder(32768);
        if(ReadTextTimeout(new IntPtr(edit),13,new IntPtr(value.Capacity),value,2,1500,out result)==IntPtr.Zero)
            throw new InvalidOperationException("Cannot read the filename edit field.");
        return value.ToString();
    }
    [DllImport("user32.dll",EntryPoint="SendMessageTimeoutW",SetLastError=true)]
    private static extern IntPtr SendNumberTimeout(IntPtr hwnd,uint message,IntPtr wParam,IntPtr lParam,uint flags,uint timeout,out IntPtr result);
    public static void ReplaceDialogFilename(long edit,string filename) {
        IntPtr result;IntPtr hwnd=new IntPtr(edit);
        if(SendNumberTimeout(hwnd,177,IntPtr.Zero,new IntPtr(-1),2,650,out result)==IntPtr.Zero)
            throw new InvalidOperationException("Cannot select filename text.");
        // EM_REPLACESEL returns zero on success: only the API timeout result is checked.
        if(SendTextTimeout(hwnd,194,new IntPtr(1),filename,2,650,out result)==IntPtr.Zero)
            throw new InvalidOperationException("Cannot replace filename text.");
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
    [StructLayout(LayoutKind.Sequential)] private struct Placement { public int Length, Flags, ShowCmd; public Point MinPosition, MaxPosition; public Rect Normal; }
    [DllImport("user32.dll")] private static extern bool GetWindowPlacement(IntPtr hwnd, ref Placement placement);
    [DllImport("user32.dll")] private static extern bool SetWindowPlacement(IntPtr hwnd, ref Placement placement);
    [DllImport("user32.dll")] private static extern bool IsZoomed(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
    // Saved as "showCmd,left,top,right,bottom" so the caller can put the window back exactly.
    public static string SavePlacement(long handle) {
        var p=new Placement(); p.Length=Marshal.SizeOf(typeof(Placement));
        if(!GetWindowPlacement(new IntPtr(handle),ref p)) return null;
        return string.Join(",",new[]{p.ShowCmd,p.Normal.Left,p.Normal.Top,p.Normal.Right,p.Normal.Bottom});
    }
    public static bool RestorePlacement(long handle,string saved) {
        if(string.IsNullOrEmpty(saved) || !IsWindow(new IntPtr(handle))) return false;
        var parts=saved.Split(','); if(parts.Length!=5) return false;
        var p=new Placement(); p.Length=Marshal.SizeOf(typeof(Placement));
        if(!GetWindowPlacement(new IntPtr(handle),ref p)) return false;
        p.ShowCmd=int.Parse(parts[0]); p.Normal.Left=int.Parse(parts[1]); p.Normal.Top=int.Parse(parts[2]);
        p.Normal.Right=int.Parse(parts[3]); p.Normal.Bottom=int.Parse(parts[4]);
        if(p.ShowCmd==2) p.ShowCmd=6;      // minimized: keep it minimized without activating
        return SetWindowPlacement(new IntPtr(handle),ref p);
    }
    public static int WindowWidth(long handle) { Rect r; return GetWindowRect(new IntPtr(handle),out r) ? r.Right-r.Left : 0; }
    // GNS3 hides whole toolbars (the Screenshot button among them) in a narrow window.
    // Maximize only when the window is narrower than minWidth; the caller restores the placement.
    public static bool Widen(long handle,int minWidth) {
        IntPtr hwnd=new IntPtr(handle);
        if(!IsWindow(hwnd) || IsZoomed(hwnd) || WindowWidth(handle)>=minWidth) return false;
        ShowWindowAsync(hwnd,3);
        var clock=Stopwatch.StartNew();
        while(!IsZoomed(hwnd) && clock.ElapsedMilliseconds<2000) Thread.Sleep(50);
        Thread.Sleep(400);
        return IsZoomed(hwnd);
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
        if(foreground) BringForeground(handle);
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
