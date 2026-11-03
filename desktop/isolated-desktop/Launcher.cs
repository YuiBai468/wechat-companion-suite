using System;

using System.Drawing;

using System.IO;

using System.Diagnostics;

using System.Reflection;

using System.Runtime.InteropServices;

using System.Windows.Forms;

using System.Web.Script.Serialization;

using Microsoft.Win32;

using System.Collections.Generic;



namespace WeChatIsolatedDesktop {

  [ComImport, Guid("302D8188-0052-4807-806A-362B628F9AC5"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]

  interface IExtendedSettings {

    void Put([MarshalAs(UnmanagedType.BStr)] string name, [In] ref object value);

    void Get([MarshalAs(UnmanagedType.BStr)] string name, [Out] out object value);

  }

  sealed class RdpView : AxHost {

    public RdpView() : base("A0C63C30-F08D-4AB4-907C-34905D770C7D") {}

    public object Client { get { return GetOcx(); } }

  }

  static class Backend {

    static Type t;

    public static object Call(string name, params object[] args) {

      if(t==null) t=Assembly.LoadFrom(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"backend","SessionBackend.dll")).GetType("WeChatIsolatedDesktop.SessionBackend",true);

      try { return t.GetMethod(name).Invoke(null,args); }

      catch(TargetInvocationException e) { throw e.InnerException ?? e; }

    }

    public static int Child { get { return (int)Call("GetChildSessionId"); } }

    public static int Session { get { return Process.GetCurrentProcess().SessionId; } }

  }

  static class Program {

    public static readonly string Root=AppDomain.CurrentDomain.BaseDirectory;

    public static string LogFile=Path.Combine(Root,"launcher.log");

    public static readonly JavaScriptSerializer Json=new JavaScriptSerializer();

    public static void Log(string message) { try { File.AppendAllText(LogFile,DateTime.Now.ToString("s")+" "+message+Environment.NewLine); } catch {} }

    public static string Quote(string value) { return "\""+value.Replace("\"","\\\"")+"\""; }

    [STAThread] static void Main(string[] args) {

      Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);

      bool worker=Array.IndexOf(args,"--worker")>=0;int parent=-1,parentPid=-1;

      for(int i=0;i+1<args.Length;i++)if(args[i]=="--parent-session")int.TryParse(args[i+1],out parent);

      for(int i=0;i+1<args.Length;i++)if(args[i]=="--parent-pid")int.TryParse(args[i+1],out parentPid);

      if(worker) {

        LogFile=Path.Combine(Root,"worker.log");

        bool validParent=false;try{validParent=parentPid>0 && Process.GetProcessById(parentPid).SessionId==parent;}catch{}

        if(parent<0 || Backend.Session==parent || !SystemInformation.TerminalServerSession || !validParent) { Log("REFUSED: worker requires a live parent and a separate RDP session");Environment.ExitCode=12;return; }

        Application.Run(new WorkerForm(parent));

      } else Application.Run(new HostForm(Array.IndexOf(args,"--no-connect")<0,Array.IndexOf(args,"--background")>=0));

    }

  }

  static class IsolationMonitor {

    [StructLayout(LayoutKind.Sequential)] struct Point {public int X,Y;}

    [DllImport("user32.dll")] static extern bool GetCursorPos(out Point p);

    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")] static extern uint GetClipboardSequenceNumber();

    public static Dictionary<string,object> Read(string file){try{return Program.Json.DeserializeObject(File.ReadAllText(Path.Combine(Program.Root,file))) as Dictionary<string,object>;}catch{return null;}}

    public static bool Matches(Dictionary<string,object> d,int parent,int child){return d!=null && d.ContainsKey("parentSessionId") && d.ContainsKey("childSessionId") && Convert.ToInt32(d["parentSessionId"])==parent && Convert.ToInt32(d["childSessionId"])==child;}

    public static bool Verified(int parent,int child){var d=Read("isolation-verdict.json");return Matches(d,parent,child)&&d.ContainsKey("verified")&&Convert.ToBoolean(d["verified"]);}

    public static void Begin(int child){

      int parent=Backend.Session;string nonce=Guid.NewGuid().ToString("N");

      new System.Threading.Thread(()=>{

        Point before;bool cursorOk=GetCursorPos(out before),cursorSame=cursorOk,focusSame=true,clipboardSame=true;long focus=GetForegroundWindow().ToInt64();uint clip=GetClipboardSequenceNumber();DateTime start=DateTime.UtcNow;

        File.WriteAllText(Path.Combine(Program.Root,"worker-command.json"),Program.Json.Serialize(new {command="probe",nonce=nonce,parentSessionId=parent,childSessionId=child}));

        var watch=Stopwatch.StartNew();while(watch.ElapsedMilliseconds<2000){Point p;if(!GetCursorPos(out p)||p.X!=before.X||p.Y!=before.Y)cursorSame=false;if(GetForegroundWindow().ToInt64()!=focus)focusSame=false;if(GetClipboardSequenceNumber()!=clip)clipboardSame=false;System.Threading.Thread.Sleep(5);}

        var pjson=Read("child-probe.json");bool probeOk=pjson!=null&&pjson.ContainsKey("ok")&&Convert.ToBoolean(pjson["ok"])&&Convert.ToInt32(pjson["sessionId"])==child&&Convert.ToInt32(pjson["parentSessionArg"])==parent&&File.GetLastWriteTimeUtc(Path.Combine(Program.Root,"child-probe.json"))>=start;

        var result=new {verified=probeOk&&cursorSame&&focusSame&&clipboardSame,nonce=nonce,parentSessionId=parent,childSessionId=child,childProbeOk=probeOk,cursorUnchanged=cursorSame,foregroundUnchanged=focusSame,clipboardUnchanged=clipboardSame,parentCursorX=before.X,parentCursorY=before.Y,time=DateTime.Now.ToString("s")};

        File.WriteAllText(Path.Combine(Program.Root,"isolation-verdict.json"),Program.Json.Serialize(result));Program.Log("Isolation verdict: "+Program.Json.Serialize(result));

      }){IsBackground=true}.Start();

    }

  }

  class BaseForm : Form {

    protected Color Bg=Color.FromArgb(14,21,33), PanelBg=Color.FromArgb(23,34,51), Fg=Color.FromArgb(231,239,249), Accent=Color.FromArgb(121,173,255);

    protected Label status=new Label();protected FlowLayoutPanel buttons=new FlowLayoutPanel();

    protected Button AddButton(string text,EventHandler handler) {

      var b=new Button {Text=text,AutoSize=true,Height=34,MinimumSize=new Size(105,34),FlatStyle=FlatStyle.Flat,BackColor=PanelBg,ForeColor=Fg,Margin=new Padding(0,0,10,6),Padding=new Padding(8,2,8,2)};

      b.FlatAppearance.BorderColor=Color.FromArgb(54,75,103);b.Click+=handler;buttons.Controls.Add(b);return b;

    }

    protected BaseForm() {

      BackColor=Bg;ForeColor=Fg;Font=new Font("Microsoft YaHei UI",9);StartPosition=FormStartPosition.CenterScreen;

      var top=new Panel {Dock=DockStyle.Top,Height=104,Padding=new Padding(16,12,16,6),BackColor=Bg};

      status.Dock=DockStyle.Top;status.Height=25;status.ForeColor=Accent;

      buttons.Dock=DockStyle.Bottom;buttons.Height=48;buttons.WrapContents=false;buttons.AutoScroll=true;

      top.Controls.Add(status);top.Controls.Add(buttons);Controls.Add(top);

    }

    protected void ShowError(Exception e) { Program.Log(e.ToString());status.Text="操作未完成："+e.Message; }

  }

  sealed class HostForm : BaseForm {

    readonly bool backgroundLaunch;

    protected override bool ShowWithoutActivation { get { return backgroundLaunch; } }

    RdpView view;object ocx;Timer timer=new Timer();NotifyIcon tray;TextBox journal;

    bool autoConnect,connecting,eventsAttached;int seenChild=-2;Button connect;

    bool bootstrapped;DateTime connectedAt=DateTime.MinValue;string childRequest="",hostCommand="",bootstrapTaskName=null;object schedulerFolder;

    string probePath=Path.Combine(Program.Root,"child-probe.json");

    public HostForm(bool auto,bool background) {

      backgroundLaunch=background;

      if(backgroundLaunch){

        Opacity=0;ShowInTaskbar=false;

        var oldHostCommand=IsolationMonitor.Read("host-command.json");

        if(oldHostCommand!=null && oldHostCommand.ContainsKey("nonce"))hostCommand=Convert.ToString(oldHostCommand["nonce"]);

        var oldProbe=IsolationMonitor.Read("probe-request-child.json");

        if(oldProbe!=null && oldProbe.ContainsKey("nonce"))childRequest=Convert.ToString(oldProbe["nonce"]);

      }

      autoConnect=auto;Text="微信 AI 伴侣 · 独立桌面";Size=new Size(1140,850);MinimumSize=new Size(800,600);

      connect=AddButton("连接独立桌面",(s,e)=>Connect());

      AddButton("运行隔离测试",(s,e)=>RunProbe());

      AddButton("启动微信",(s,e)=>SendWorkerCommand("wechat"));

      AddButton("收起到托盘",(s,e)=>Hide());

      AddButton("打开监控看板",(s,e)=>Process.Start("http://127.0.0.1:8765/console"));

      journal=new TextBox {Dock=DockStyle.Bottom,Height=86,Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Vertical,BackColor=Bg,ForeColor=Fg,BorderStyle=BorderStyle.None};Controls.Add(journal);

      view=new RdpView {Dock=DockStyle.Fill};((System.ComponentModel.ISupportInitialize)view).BeginInit();Controls.Add(view);view.BringToFront();((System.ComponentModel.ISupportInitialize)view).EndInit();

      tray=new NotifyIcon {Icon=SystemIcons.Application,Text="微信 AI 伴侣 · 独立桌面",Visible=true};tray.DoubleClick+=(s,e)=>{Show();WindowState=FormWindowState.Normal;Activate();};

      var menu=new ContextMenuStrip();menu.Items.Add("打开独立桌面",null,(s,e)=>{Show();WindowState=FormWindowState.Normal;Activate();});menu.Items.Add("退出启动器",null,(s,e)=>{if(view!=null){try{dynamic c=ocx;if(c!=null && c.Connected!=0)c.Disconnect();}catch{}}tray.Visible=false;Application.Exit();});tray.ContextMenuStrip=menu;

      FormClosing+=(s,e)=>{if(e.CloseReason==CloseReason.UserClosing){e.Cancel=true;Hide();}};

      Shown+=(s,e)=>{if(backgroundLaunch){Hide();Opacity=1;ShowInTaskbar=true;}try{Log((string)Backend.Call("StatusJson"));status.Text="待连接 · 微信和机器人只在独立会话内启动";if(autoConnect)Connect();}catch(Exception ex){ShowError(ex);}};

      timer.Interval=1000;timer.Tick+=(s,e)=>Poll();timer.Start();

    }

    void Log(string value){Program.Log(value);if(journal.TextLength>14000)journal.Clear();journal.AppendText(DateTime.Now.ToString("HH:mm:ss")+" "+value+Environment.NewLine);}

    void SetExtended(string name,object value,bool required){try{((IExtendedSettings)ocx).Put(name,ref value);Log("设置 "+name+"="+value);}catch(Exception e){if(required)throw;Log("可选设置不支持："+name+" ("+e.HResult.ToString("X")+")");}}

    void Connect(){

      try{

        if(!(bool)Backend.Call("IsChildSessionsEnabled"))throw new InvalidOperationException("系统尚未启用子会话；启动器不会自行改变系统设置。");

        ocx=view.Client;dynamic c=ocx;

        if(c.Connected!=0){Log("会话已在连接或已连接。");return;}

        c.Server="localhost";c.DesktopWidth=1280;c.DesktopHeight=800;c.ColorDepth=32;

        SetExtended("ConnectToChildSession",true,true);

        object flag;((IExtendedSettings)ocx).Get("ConnectToChildSession",out flag);

        if(!(flag is bool) || !(bool)flag)throw new InvalidOperationException("子会话模式未确认，拒绝连接普通远程桌面。");

        c.AdvancedSettings7.EnableCredSspSupport=true;

        c.AdvancedSettings7.RedirectClipboard=false;

        if((bool)c.AdvancedSettings7.RedirectClipboard)throw new InvalidOperationException("未能关闭剪贴板同步，拒绝连接。");

        c.AdvancedSettings7.RedirectDrives=false;c.AdvancedSettings7.RedirectPrinters=false;

        c.AdvancedSettings7.SmartSizing=true;

        SetExtended("IgnoreCursors",true,false);SetExtended("IgnoreServerGeneratedMouseMoves",true,false);SetExtended("EnableFrameBufferRedirection",true,false);

        SetExtended("RemoteDesktop_SuppressWhenMinimized",(uint)2,false);

        c.SecuredSettings.StartProgram=Program.Quote(Application.ExecutablePath)+" --worker --parent-session "+Backend.Session+" --parent-pid "+Process.GetCurrentProcess().Id;

        c.SecuredSettings.WorkDir=Program.Root;c.SecuredSettings.KeyboardHookMode=0;

        if(!eventsAttached){

          Guid ev=new Guid("336D5562-EFA8-482E-8CB3-C5C0FC7A7DB6");

          ComEventsHelper.Combine(ocx,ev,2,new Action(()=>BeginInvoke(new Action(()=>Log("RDP 已连接，等待子会话就绪。")))));

          ComEventsHelper.Combine(ocx,ev,4,new Action<int>(reason=>BeginInvoke(new Action(()=>{connecting=false;Log("RDP 断开，原因码 "+reason);status.Text="连接已断开 · 原因码 "+reason;}))));

          eventsAttached=true;

        }

        connecting=true;status.Text="正在创建独立子会话…";Log("只连接本机子会话；剪贴板、磁盘与打印机重定向已关闭。");c.Connect();

      }catch(Exception e){connecting=false;ShowError(e);Log(e.Message);}

    }

    void Poll(){

      try{

        int child=Backend.Child;

        if(child!=seenChild){seenChild=child;connectedAt=DateTime.Now;Log(child>=0?"已确认独立会话 ID="+child+"，父会话 ID="+Backend.Session:"尚无已连接的独立会话。");}

        if(child>=0){connecting=false;status.Text=IsolationMonitor.Verified(Backend.Session,child)?"隔离验证通过 · 子会话 "+child+" · 主桌面光标、焦点和剪贴板未变化":"独立桌面已连接 · 会话 "+child+" · 隔离待验证";

          var worker=IsolationMonitor.Read("child-worker-state.json");bool ready=false;

          if(worker!=null && worker.ContainsKey("processId") && Convert.ToInt32(worker["sessionId"])==child){try{var wp=Process.GetProcessById(Convert.ToInt32(worker["processId"]));ready=wp.SessionId==child && String.Equals(wp.MainModule.FileName,Application.ExecutablePath,StringComparison.OrdinalIgnoreCase);}catch{}}

          if(!ready && !bootstrapped && (DateTime.Now-connectedAt).TotalSeconds>2)Bootstrap(child);

          if(ready && bootstrapTaskName!=null){dynamic f=schedulerFolder;f.DeleteTask(bootstrapTaskName,0);Log("一次性会话启动任务已移除。");bootstrapTaskName=null;}

          if(!ready && bootstrapTaskName!=null && (DateTime.Now-connectedAt).TotalSeconds>25){dynamic f=schedulerFolder;f.DeleteTask(bootstrapTaskName,0);bootstrapTaskName=null;Log("启动面板未能就绪，临时任务已清理；请查看日志。");}

          var request=IsolationMonitor.Read("probe-request-child.json");if(IsolationMonitor.Matches(request,Backend.Session,child)&&Convert.ToString(request["nonce"])!=childRequest){childRequest=Convert.ToString(request["nonce"]);RunProbe();}

          var hc=IsolationMonitor.Read("host-command.json");if(hc!=null&&hc.ContainsKey("nonce")&&Convert.ToString(hc["nonce"])!=hostCommand){hostCommand=Convert.ToString(hc["nonce"]);if(Convert.ToString(hc["command"])=="hide")Hide();else if(Convert.ToString(hc["command"])=="show"){Show();WindowState=FormWindowState.Normal;}}

        }

        File.WriteAllText(Path.Combine(Program.Root,"host-state.json"),Program.Json.Serialize(new {processId=Process.GetCurrentProcess().Id,parentSessionId=Backend.Session,childSessionId=child,visible=Visible,backgroundLaunch=backgroundLaunch,updatedAt=DateTime.UtcNow.ToString("o")}));

      }catch(Exception e){if(connecting)Log(e.Message);}

    }

    void Bootstrap(int child){

      bootstrapped=true;try{

        if(child<1||child==Backend.Session||Backend.Child!=child)throw new InvalidOperationException("子会话身份未确认。");

        dynamic scheduler=Activator.CreateInstance(Type.GetTypeFromProgID("Schedule.Service"));scheduler.Connect();dynamic folder=scheduler.GetFolder(@"\");schedulerFolder=folder;

        dynamic definition=scheduler.NewTask(0);definition.Principal.LogonType=3;definition.Principal.RunLevel=0;

        definition.Settings.AllowDemandStart=true;definition.Settings.DisallowStartIfOnBatteries=false;definition.Settings.StopIfGoingOnBatteries=false;definition.Settings.ExecutionTimeLimit="PT0S";

        dynamic action=definition.Actions.Create(0);action.Path=Application.ExecutablePath;action.Arguments="--worker --parent-session "+Backend.Session+" --parent-pid "+Process.GetCurrentProcess().Id;action.WorkingDirectory=Program.Root;

        string sid=System.Security.Principal.WindowsIdentity.GetCurrent().User.Value;bootstrapTaskName="WeChatChildBootstrap-"+Guid.NewGuid().ToString("N");

        dynamic registered=folder.RegisterTaskDefinition(bootstrapTaskName,definition,2,sid,null,3,null);registered.RunEx(null,4,child,null);Log("仅以当前用户身份向已确认的子会话启动面板。");

      }catch(Exception e){if(bootstrapTaskName!=null&&schedulerFolder!=null){try{dynamic f=schedulerFolder;f.DeleteTask(bootstrapTaskName,0);}catch{}bootstrapTaskName=null;}ShowError(e);}

    }

    void SendWorkerCommand(string command){try{int child=Backend.Child;if(child<1)throw new InvalidOperationException("独立桌面尚未连接。");File.WriteAllText(Path.Combine(Program.Root,"worker-command.json"),Program.Json.Serialize(new {command=command,nonce=Guid.NewGuid().ToString("N"),parentSessionId=Backend.Session,childSessionId=child}));}catch(Exception e){ShowError(e);}}

    void RunProbe(){try{int child=Backend.Child;if(child<0)throw new InvalidOperationException("请先连接独立桌面。");var w=IsolationMonitor.Read("child-worker-state.json");if(w==null||Convert.ToInt32(w["sessionId"])!=child)throw new InvalidOperationException("子会话启动面板尚未就绪。");IsolationMonitor.Begin(child);Log("隔离测试开始，请在两秒内暂停移动鼠标和切换窗口。");}catch(Exception e){ShowError(e);}}

  }

  sealed class WorkerForm : BaseForm {

    readonly int parent;Label info=new Label();Timer commandTimer=new Timer();string lastCommand="";

    public WorkerForm(int parentSession){

      parent=parentSession;Text="微信 AI 伴侣 · 子会话启动面板";Size=new Size(650,370);MinimumSize=new Size(600,330);

      status.Text="当前会话 "+Backend.Session+" · 父会话 "+parent+" · 输入在本会话内处理";

      AddButton("运行隔离测试",(s,e)=>RunProbe());AddButton("启动微信",(s,e)=>LaunchWeChat());AddButton("检查机器人",(s,e)=>CheckBot());AddButton("启动机器人",(s,e)=>LaunchBot());

      info.Dock=DockStyle.Fill;info.Padding=new Padding(22,22,22,12);info.ForeColor=Fg;info.Text="这是机器人专用的独立 Windows 会话。\n\n先运行隔离测试，再启动微信并登录机器人小号。\n确认登录后，点击“启动机器人”。\n\n关闭父会话启动器会断开连接；父用户注销会结束子会话。";Controls.Add(info);info.BringToFront();

      Shown+=(s,e)=>{File.WriteAllText(Path.Combine(Program.Root,"child-worker-state.json"),Program.Json.Serialize(new {sessionId=Backend.Session,parentSessionId=parent,processId=Process.GetCurrentProcess().Id,started=DateTime.Now.ToString("s")}));};

      commandTimer.Interval=100;commandTimer.Tick+=(s,e)=>{try{var c=IsolationMonitor.Read("worker-command.json");if(!IsolationMonitor.Matches(c,parent,Backend.Session))return;string nonce=Convert.ToString(c["nonce"]);if(nonce==lastCommand)return;lastCommand=nonce;Guard();string cmd=Convert.ToString(c["command"]);if(cmd=="probe")StartProbe();else if(cmd=="wechat")LaunchWeChat();else if(cmd=="check")CheckBot();}catch(Exception ex){Program.Log(ex.Message);}};commandTimer.Start();

    }

    void Guard(){if(Backend.Session==parent || parent<0)throw new InvalidOperationException("拒绝在主桌面运行。");}

    void RunProbe(){try{Guard();File.WriteAllText(Path.Combine(Program.Root,"probe-request-child.json"),Program.Json.Serialize(new {nonce=Guid.NewGuid().ToString("N"),parentSessionId=parent,childSessionId=Backend.Session}));status.Text="隔离测试已请求，请在两秒内暂停操作。";}catch(Exception e){ShowError(e);}}

    void StartProbe(){Guard();Process.Start(new ProcessStartInfo(Path.Combine(Program.Root,"backend","ChildProbe.exe"),"--parent-session "+parent+" --out "+Program.Quote(Path.Combine(Program.Root,"child-probe.json"))){UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=Program.Root});var captureTimer=new Timer{Interval=300};captureTimer.Tick+=(s,e)=>{captureTimer.Stop();captureTimer.Dispose();try{var pt=PointToScreen(new Point(ClientSize.Width-25,ClientSize.Height-25));using(var bmp=new Bitmap(1,1)){using(var g=Graphics.FromImage(bmp))g.CopyFromScreen(pt,Point.Empty,new Size(1,1));Color actual=bmp.GetPixel(0,0);File.WriteAllText(Path.Combine(Program.Root,"capture-check.json"),Program.Json.Serialize(new {ok=actual.ToArgb()==Bg.ToArgb(),sessionId=Backend.Session,actual=actual.ToArgb(),expected=Bg.ToArgb(),time=DateTime.Now.ToString("s")}));}}catch(Exception ex){Program.Log("Capture check: "+ex.Message);}};captureTimer.Start();}

    void CheckBot(){

      try{Guard();string entry=Path.Combine(Program.Root,"isolated-bot.py");if(!File.Exists(entry))throw new InvalidOperationException("检查入口尚未准备好。");status.Text="正在只读检查当前会话的微信与账号绑定…";

        new System.Threading.Thread(()=>{try{var py=Environment.GetEnvironmentVariable("COMPANION_PYTHON"); if(string.IsNullOrEmpty(py)){var venv=Path.Combine(Program.Root,"..","..",".venv","Scripts","python.exe"); py=File.Exists(venv)?Path.GetFullPath(venv):"python.exe";} var start=new ProcessStartInfo(py,"-X utf8 "+Program.Quote(entry)+" --parent-session "+parent+" --expected-session "+Backend.Session+" --check"){UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=Program.Root,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=System.Text.Encoding.UTF8,StandardErrorEncoding=System.Text.Encoding.UTF8};using(var p=Process.Start(start)){var output=p.StandardOutput.ReadToEndAsync();var error=p.StandardError.ReadToEndAsync();if(!p.WaitForExit(120000)){p.Kill();throw new TimeoutException("检查超时，未启动机器人。");}var result=new {exitCode=p.ExitCode,output=output.Result.Trim(),time=DateTime.Now.ToString("s"),sessionId=Backend.Session};File.WriteAllText(Path.Combine(Program.Root,"bot-preflight-output.json"),Program.Json.Serialize(result));int checkExitCode=p.ExitCode;BeginInvoke(new Action(()=>status.Text=checkExitCode==0?"只读检查通过，可启动机器人。":"只读检查未通过，未启动机器人。"));}}catch(Exception ex){Program.Log("Preflight: "+ex.Message);BeginInvoke(new Action(()=>status.Text="检查未完成："+ex.Message));}}){IsBackground=true}.Start();

      }catch(Exception e){ShowError(e);}

    }

    void LaunchWeChat(){

      try{Guard();string[] paths={@"C:\Program Files\Tencent\Weixin\Weixin.exe",@"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",@"C:\Program Files\Tencent\WeChat\WeChat.exe",@"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe",@"D:\Weixin\Weixin.exe",@"D:\Program Files\Tencent\Weixin\Weixin.exe",@"D:\Program Files (x86)\Tencent\Weixin\Weixin.exe"};

        string found=null;foreach(string p in paths)if(File.Exists(p)){found=p;break;}

        if(found==null)foreach(string hive in new[]{"HKEY_CURRENT_USER","HKEY_LOCAL_MACHINE"})foreach(string app in new[]{"Weixin.exe","WeChat.exe"}){string p=Registry.GetValue(hive+@"\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\"+app,"",null) as string;if(p!=null && File.Exists(p.Trim('"')))found=p.Trim('"');}

        if(found==null){using(var dlg=new OpenFileDialog{Title="选择微信主程序",Filter="微信主程序|Weixin.exe;WeChat.exe",CheckFileExists=true}){if(dlg.ShowDialog()!=DialogResult.OK)return;found=dlg.FileName;}}

        Process.Start(new ProcessStartInfo(found){UseShellExecute=true,WorkingDirectory=Path.GetDirectoryName(found)});status.Text="微信已在子会话启动，请自行完成登录。";

      }catch(Exception e){ShowError(e);}

    }

    void LaunchBot(){

      try{Guard();bool exists=false;foreach(var p in Process.GetProcessesByName("Weixin"))if(p.SessionId==Backend.Session)exists=true;foreach(var p in Process.GetProcessesByName("WeChat"))if(p.SessionId==Backend.Session)exists=true;

        if(!exists)throw new InvalidOperationException("请先在本子会话启动并登录微信。");

        if(!IsolationMonitor.Verified(parent,Backend.Session))throw new InvalidOperationException("请先完成隔离测试，并确认主桌面光标、焦点及剪贴板均未变化。");

        if(MessageBox.Show(this,"启动后机器人会按已有配置自动回复，并可能主动发消息。确认微信已登录机器人小号？","启动自动回复",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;

        string script=Path.Combine(Program.Root,"isolated-bot.cmd");

        if(!File.Exists(script))throw new InvalidOperationException("绑定当前会话的机器人入口尚未准备好。");

        Process.Start(new ProcessStartInfo("cmd.exe","/c "+Program.Quote(script)+" "+parent+" "+Backend.Session){UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=Program.Root});status.Text="机器人已在子会话启动；请查看监控看板确认运行。";

      }catch(Exception e){ShowError(e);}

    }

  }

}

