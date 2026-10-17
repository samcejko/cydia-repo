Add-Type -AssemblyName System.Drawing

$srcIconPath = "c:\Users\samcejko\Documents\southpark-ios6\tools\icon.png"
$bannerPath = "c:\Users\samcejko\Documents\cydia-repo\site\banners\com.samcejko.cityvod.png"

$w = 526
$h = 296

$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

# Background gradient: dark vignette with rich crimson center glow
$rect = New-Object System.Drawing.Rectangle 0, 0, $w, $h
$bgBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush ($rect, [System.Drawing.Color]::FromArgb(255, 38, 12, 14), [System.Drawing.Color]::FromArgb(255, 14, 8, 10), [System.Drawing.Drawing2D.LinearGradientMode]::Vertical)
$g.FillRectangle($bgBrush, $rect)
$bgBrush.Dispose()

# Draw radial-like glow in center
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$path.AddEllipse(-50, -50, $w + 100, $h + 100)
$pbg = New-Object System.Drawing.Drawing2D.PathGradientBrush $path
$pbg.CenterColor = [System.Drawing.Color]::FromArgb(60, 235, 45, 55)
$pbg.SurroundColors = @([System.Drawing.Color]::FromArgb(0, 14, 8, 10))
$g.FillRectangle($pbg, $rect)
$pbg.Dispose()
$path.Dispose()

# Load icon
$icon = [System.Drawing.Image]::FromFile($srcIconPath)

# We draw the icon in center with size 230x230, rounded corners and shadow
$iconSize = 230
$x = [int](($w - $iconSize) / 2)
$y = [int](($h - $iconSize) / 2)

# Shadow
for ($s = 10; $s -gt 0; $s--) {
    $alpha = [int](15 - $s)
    if ($alpha -lt 1) { $alpha = 1 }
    $sBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb($alpha, 0, 0, 0))
    $g.FillEllipse($sBrush, ($x - $s), ($y + 8 - $s), ($iconSize + $s * 2), ($iconSize + $s * 2))
    $sBrush.Dispose()
}

# Icon draw
$iconRect = New-Object System.Drawing.Rectangle $x, $y, $iconSize, $iconSize
$iconPath = New-Object System.Drawing.Drawing2D.GraphicsPath
$radius = 44
$dia = $radius * 2
$iconPath.AddArc($x, $y, $dia, $dia, 180, 90)
$iconPath.AddArc($x + $iconSize - $dia, $y, $dia, $dia, 270, 90)
$iconPath.AddArc($x + $iconSize - $dia, $y + $iconSize - $dia, $dia, $dia, 0, 90)
$iconPath.AddArc($x, $y + $iconSize - $dia, $dia, $dia, 90, 90)
$iconPath.CloseFigure()

$state = $g.Save()
$g.SetClip($iconPath)
$g.DrawImage($icon, $iconRect)
$g.Restore($state)

# Border highlight around icon
$borderPen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(80, 255, 255, 255)), 2
$g.DrawPath($borderPen, $iconPath)
$borderPen.Dispose()
$iconPath.Dispose()

$icon.Dispose()
$g.Dispose()

$bmp.Save($bannerPath, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

Write-Host "Created banner: $bannerPath ($w x $h)"
