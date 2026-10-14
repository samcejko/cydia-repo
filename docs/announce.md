# Announcement drafts

## Reddit (r/LegacyJailbreak), English

**Title:** I built a Cydia repo of new apps for iOS 6 (iPad 2 / iPhone 4S era): Twitch, YouTube, TikTok, Spotify, Kick, GeForce NOW, Minecraft: Java Edition, an AI chat and a VS Code editor

**Body:**

Over the last weeks I have been writing apps for jailbroken iOS 6 devices and I finally put them into one repository
so they install and update through Cydia (Sileo, Zebra and Installer work too):

**http://repo.samcejko.eu/** (Cydia → Sources → Edit → Add; it has to be plain http, iOS 6 does not trust today's
certificates)

What is in it:

- **Twitcher**: Twitch with live streams, chat (BTTV/FFZ/7TV emotes), past broadcasts with chat replay, clips, optional login
- **Tubie**: YouTube in the look of the old built-in app: videos, shorts, live streams, channels, comments, SponsorBlock, captions
- **TikTak**: a TikTok viewer with a For You feed that learns on the device (needs a small yt-dlp helper on your own server)
- **Spot6**: Spotify in the look of the 2012 iPad app, up to 320 kbit/s, synced lyrics, Spotify Connect (needs Premium)
- **Kicker**: Kick with live streams, chat, past broadcasts, clips
- **GFN6**: GeForce NOW streaming on a 2012 iPad: WebRTC built from scratch, hardware H.264, touch gamepad (needs a GFN account)
- **MC6**: a launcher of Minecraft: Java Edition: Java 8 ported to 32-bit iOS, LWJGL 2 on gl4es; every version from
  RubyDung to 1.12.2 plays on an iPad 2 (you need to own the game, it downloads from Mojang)
- **AI Chat**: an OpenRouter chat client with your own API key, Markdown and LaTeX, images, files
- **Kodium**: the VS Code workbench running on an iPad 2, with a terminal (early)

**Not only iOS 6:** every app except MC6 and Kodium also has a 64-bit build for iOS 14 to 17, with the same iOS 6
look: a rootless jailbreak (Dopamine, palera1n) installs it from the same repository in Sileo or Zebra, TrollStore
users take the `-arm64.ipa` from the GitHub releases.

The trick that makes most of them possible: every app ships its own TLS 1.2 stack (Mbed TLS), so a 2012 system can talk
to today's servers, and the video goes to the old system player through a tiny local proxy. No relay servers (except
TikTak's helper, which runs on your own machine). English and Czech.

Everything is free and open source (github.com/samcejko). Tested on an iPad 2 with iOS 6.1.3; the iPhone layouts exist
but I have not tried them on a real iPhone yet, so reports are welcome. If something is useful to you, there is a
Ko-fi link in each app's Settings → About.

Unofficial apps, not affiliated with any of the services.

## Discord / short version

New Cydia repo for iOS 6 (iPad 2 era): http://repo.samcejko.eu/ – Twitch, YouTube, TikTok, Spotify, Kick, GeForce NOW,
Minecraft: Java Edition, an AI chat and a VS Code editor, all with their own TLS stack so they work on a 2012 system.
Free, open source, English + Czech. Source: github.com/samcejko

## Česky (krátce)

Udělal jsem vlastní Cydia repozitář pro iOS 6 (iPad 2, iPhone 4S): http://repo.samcejko.eu/ – Twitch, YouTube, TikTok,
Spotify, Kick, GeForce NOW, Minecraft: Java Edition, AI chat a editor kódu (VS Code). Každá appka má vlastní TLS vrstvu,
takže systém z roku 2012 mluví s dnešními servery. Zdarma, open source, česky i anglicky. Kdo chce, může přispět na kafe:
ko-fi.com/samcejko.
