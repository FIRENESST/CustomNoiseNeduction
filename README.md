# 便携式降噪软件

便携式降噪软件是 Windows 实时麦克风降噪工具。它从环境中学习一小段底噪，在麦克风输入时持续处理风扇声、空调声或稳定电流声。降噪后的声音可以通过软件自带的虚拟麦克风「便携式降噪麦克风」提供给会议、直播等其他软件。

## 便携版

Windows x64 上双击 `dist\便携式降噪软件.exe` 即可打开。运行库已包含在这个 EXE 中。保存的底噪预设位于 EXE 同目录的 `presets.json`，迁移时可一起复制。

## 启动麦克风

打开软件，选择麦克风和输入通道。输出默认仅显示电平。需要听到效果时，先在 **输出** 中选耳机，再点击 **启动麦克风**。使用自动学习时，启动后保持安静约 1.5 秒，采集环境底噪。降噪模式会在首份底噪就绪后开始输出。

运行时可调节 **降噪强度** 和 **最大衰减**，用 **原音** 与 **降噪** 切换对比。环境变化后，保持安静并点 **学习底噪**。点击 **停止麦克风** 可释放音频设备。

## 保存和使用底噪预设

学习完成后点 **保存预设**，给这组底噪命名。停止麦克风后也可保存刚学到的底噪。预设包含频谱、采样率、麦克风通道、降噪强度和最大衰减。

下次使用时，启动麦克风前在 **底噪预设** 下拉框选中名称。设备可用时会恢复采样时的麦克风与通道，并按保存的采样率直接启动。换设备时可以选 **启动时自动学习**。

## 给会议或直播软件使用

软件自带一个虚拟麦克风驱动。在 **设备** 中点 **降噪麦克风** 旁的 **安装**，确认 Windows 的管理员权限请求后，系统里会多出一个录音设备 **便携式降噪麦克风**。之后只要降噪软件在运行并已启动麦克风，降噪后的声音就会实时送到这个设备。会议或直播软件把麦克风选为 **便携式降噪麦克风** 即可。状态显示 **使用中** 表示已有应用在从它录音。

降噪软件读取的仍然是 Windows 里已有的物理麦克风，自己的降噪麦克风不会出现在 **麦克风** 列表中，因此不会形成回环。**输出** 只用于耳机监听，选 **仅显示电平** 也不影响降噪麦克风。停止麦克风后可以点 **卸载** 移除驱动。

让跟随系统默认输入的应用使用降噪声音时，点 **应用接入**，打开 Windows 声音设置，再把 **便携式降噪麦克风** 设为默认输入。固定选择物理麦克风的应用需要在各自的设置里切换。 [Microsoft 声音设置说明](https://support.microsoft.com/en-au/windows/hardware/drivers/fix-microphone-problems)

也可以不装驱动，改用虚拟音频线：把 **输出** 选为虚拟线的播放端，目标软件选择这条线的录音端。以 VB-CABLE 为例，降噪软件选 **CABLE Input**，目标软件选 **CABLE Output**。 [VB-CABLE 官方说明](https://vb-audio.com/Cable/)

## 从源码运行和构建

在 Windows x64 的 Python 3.10 或更新环境中安装项目依赖，然后双击 `start.cmd` 启动源码版。源码版预设位于项目目录。

```powershell
python -m pip install -r requirements.txt
```

使用独立的 Windows x64 Python 3.13 环境，可按发布版所用的主要依赖重新构建。

```powershell
python -m pip install numpy==2.1.3 PyQt5==5.15.11 PyQt5-Qt5==5.15.2 sounddevice==0.5.6 PyInstaller==6.22.3
python -m PyInstaller --distpath dist --workpath build portable.spec
```

打包时 `portable.spec` 会把驱动包放进 EXE：优先使用 `driver\signed` 中的微软签名驱动包，没有时使用 `driver\package` 中的本地构建。

默认使用 WASAPI。如果设备无法打开，可以在停止状态下切换音频接口。应用将选定的麦克风通道作为单声道处理，48 kHz 时算法缓冲约 16 毫秒。监听延迟还包括设备缓冲。降噪麦克风另有约 30 毫秒的抖动缓冲，用来吸收两个设备之间的时钟差。

## 降噪麦克风驱动

驱动源码在 `driver\NoiseMic`，基于微软的 SimpleAudioSample 示例（MS-PL 许可，见 `driver\NoiseMic\LICENSE-MS-PL.txt`）。它注册一个根枚举设备 `Root\PortableNoiseMic`，只提供一个 48 kHz、16 位、单声道的录音端点。软件通过驱动的私有 KS 属性写入降噪后的音频，驱动在 WaveRT 录音缓冲里交给 Windows。

构建需要 Visual Studio 的 **Windows Driver Kit** 组件（在 Visual Studio Installer 的“单个组件”中添加）。WDK 本身由脚本从 NuGet 下载到 `driver\packages`。

```powershell
powershell -ExecutionPolicy Bypass -File driver\build.ps1
powershell -ExecutionPolicy Bypass -File driver\tests\run_tests.ps1
```

第一条生成 `driver\package`（`NoiseMic.inf`、`NoiseMic.sys`、`noisemic.cat`），第二条运行驱动缓冲逻辑的单元测试。

Windows 只加载签过名的驱动：

- **测试**：在关闭安全启动、开启测试签名（`bcdedit /set testsigning on`）的虚拟机里测试。用 `build.ps1 -NewTestCertificate` 生成测试证书并签名，把 `driver\package\NoiseMicTest.cer` 导入虚拟机的“受信任的根证书颁发机构”和“受信任的发布者”。
- **发布**：普通用户的电脑开着安全启动，驱动必须经过微软签名。需要 EV 代码签名证书和 Microsoft 合作伙伴中心的硬件开发者账号。运行 `build.ps1 -CertificateThumbprint <EV 证书指纹> -Cab`，把生成的 `driver\build\NoiseMic.cab` 提交到合作伙伴中心做认证签名（attestation），再把返回包中的 `NoiseMic.inf`、`NoiseMic.sys`、`noisemic.cat` 放进 `driver\signed`，然后重新打包 EXE。
