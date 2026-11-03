using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
class OpenCompanion {
  [STAThread] static int Main(){
    string root=AppDomain.CurrentDomain.BaseDirectory;
    string script=Path.Combine(root,"start-suite.ps1");
    string shell=Environment.GetEnvironmentVariable("COMPANION_PWSH") ?? "pwsh.exe";
    try { if(!File.Exists(script)) { MessageBox.Show("启动文件尚未就绪，请检查套件的安装目录。","微信 AI 伴侣"); return 2; } } catch { return 2; }
    if(!File.Exists(shell) && shell.IndexOf("pwsh",StringComparison.OrdinalIgnoreCase)>=0) shell="powershell.exe";
    if(!File.Exists(shell) && shell.IndexOf("powershell",StringComparison.OrdinalIgnoreCase)<0){MessageBox.Show("启动文件尚未就绪，请检查微信 AI 伴侣的安装目录。","微信 AI 伴侣");return 2;}
    try{
      var start=new ProcessStartInfo(shell,"-NoProfile -File \""+script+"\""){UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden,WorkingDirectory=root,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
      using(var process=Process.Start(start)){
        var output=process.StandardOutput.ReadToEndAsync();var error=process.StandardError.ReadToEndAsync();
        if(!process.WaitForExit(420000)){MessageBox.Show("后台仍在准备，请稍候。可通过独立桌面的托盘图标查看状态。","微信 AI 伴侣");return 3;}
        if(process.ExitCode!=0){
          string reason="后台未能完成启动。可通过独立桌面的托盘图标查看状态。";
          try{var payload=new JavaScriptSerializer().Deserialize<System.Collections.Generic.Dictionary<string,object>>(output.Result.Trim());if(payload.ContainsKey("reason"))reason=Convert.ToString(payload["reason"]);}catch{}
          MessageBox.Show(reason,"微信 AI 伴侣 · 启动未完成");return process.ExitCode;
        }
      }
      return 0;
    }
    catch(Exception){MessageBox.Show("未能启动，请通过独立桌面托盘图标查看状态。","微信 AI 伴侣");return 1;}
  }
}
