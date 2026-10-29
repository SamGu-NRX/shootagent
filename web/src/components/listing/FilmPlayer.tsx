import { Pause, Play, Volume2, VolumeX } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { shotAt } from "../../listing/storyboard";

export interface FilmPlayerHandle {
  /** Jump to a time in the current language and play. */
  seek(seconds: number): void;
}

interface Props {
  lang: string;
  /** film URL per language; "" when there is no rendered film (demo data) */
  url: string;
  poster: string | null;
  shots: { times: Record<string, [number, number]> }[];
  /** photo for a shot, shown in place of the film when `url` is empty */
  frameFor: (shot: number) => string | null;
  onShot?: (index: number) => void;
  variant: "desk" | "phone";
  labels: { film: string; play: string; pause: string; sound: string; mute: string };
}

/**
 * The 9:16 film. A language switch keeps the viewer on the same shot: the films differ in length per language
 * (32.5 s English, 39.5 s Spanish for the 7-photo house), so the new film starts where that shot starts.
 */
export const FilmPlayer = forwardRef<FilmPlayerHandle, Props>(function FilmPlayer(
  { lang, url, poster, shots, frameFor, onShot, variant, labels },
  ref,
) {
  const video = useRef<HTMLVideoElement>(null);
  const [shot, setShot] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(variant === "phone");
  // The buyer page opens on the film playing silently, the way listing reels play in a feed; not when the
  // visitor asked for reduced motion.
  const [autoPlay] = useState(() => variant === "phone" && !matchMedia("(prefers-reduced-motion: reduce)").matches);
  const pendingSeek = useRef<number | null>(null);
  const lastLang = useRef(lang);
  const shotRef = useRef(0);

  const report = (i: number) => {
    if (i < 0 || i === shotRef.current) return;
    shotRef.current = i;
    setShot(i);
    onShot?.(i);
  };

  useImperativeHandle(ref, () => ({
    seek(seconds: number) {
      const v = video.current;
      const i = shotAt(shots, lang, seconds + 0.01);
      if (!v || !url) {
        report(i >= 0 ? i : 0);
        return;
      }
      v.currentTime = seconds;
      void v.play().catch(() => {});
    },
  }));

  // language switch: remember which shot was on screen, then seek the new film to that shot's start
  useEffect(() => {
    if (lastLang.current === lang) return;
    const start = shots[shotRef.current]?.times[lang]?.[0];
    pendingSeek.current = start ?? 0;
    lastLang.current = lang;
  }, [lang, shots]);

  const onLoaded = () => {
    const v = video.current;
    if (v && pendingSeek.current != null) {
      v.currentTime = pendingSeek.current;
      pendingSeek.current = null;
      if (playing) void v.play().catch(() => {});
    }
  };

  const toggle = () => {
    const v = video.current;
    if (!v) return;
    if (v.paused) void v.play().catch(() => {});
    else v.pause();
  };

  if (!url) {
    const frame = frameFor(shot) ?? poster;
    return (
      <div className="film" data-variant={variant}>
        {frame && <img className="film-frame" src={frame} alt="" />}
        <p className="film-demo-note">Demo data: the rendered film plays on the live server.</p>
      </div>
    );
  }

  return (
    <div className="film" data-variant={variant}>
      <video
        ref={video}
        key={url}
        className="film-video"
        src={url}
        poster={poster ?? undefined}
        playsInline
        muted={muted}
        autoPlay={autoPlay}
        preload={autoPlay ? "auto" : "metadata"}
        controls={variant === "desk"}
        controlsList="nodownload noplaybackrate"
        disablePictureInPicture
        aria-label={labels.film}
        onLoadedMetadata={onLoaded}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => report(shotAt(shots, lang, e.currentTarget.currentTime))}
        onClick={variant === "phone" ? toggle : undefined}
      />
      {variant === "phone" && (
        <>
          {!playing && (
            <button className="film-play" onClick={toggle} aria-label={labels.play}>
              <Play size={26} fill="currentColor" aria-hidden />
            </button>
          )}
          <div className="film-controls">
            {playing && (
              <button className="film-btn" onClick={toggle} aria-label={labels.pause}>
                <Pause size={16} aria-hidden />
              </button>
            )}
            <button className="film-btn" onClick={() => setMuted((m) => !m)} aria-label={muted ? labels.sound : labels.mute} aria-pressed={!muted}>
              {muted ? <VolumeX size={16} aria-hidden /> : <Volume2 size={16} aria-hidden />}
            </button>
          </div>
        </>
      )}
    </div>
  );
});
