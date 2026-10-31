import {AbsoluteFill, Audio, Img, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {loadFont} from '@remotion/google-fonts/Montserrat';

const {fontFamily: MONT} = loadFont('normal', {weights: ['900'], subsets: ['cyrillic', 'latin']});

type Word = {t: string; s: number; e: number};
type Phrase = {words: Word[]; s: number; e: number};
type Segment = {src: string; kind: 'video' | 'image'; dur: number};
export type MontageProps = {fps: number; width: number; height: number; segments: Segment[]; phrases: Phrase[]; audio: string | null; accent: string};

// Кадр без движения = стоп-кадр в чек-листе монтажа, поэтому на картинке всегда медленный наезд
const Still: React.FC<{src: string; dur: number}> = ({src, dur}) => {
	const fr = useCurrentFrame();
	const {fps} = useVideoConfig();
	const z = interpolate(fr, [0, dur * fps], [1.0, 1.08]);
	return <Img src={staticFile(src)} style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${z})`}} />;
};

const Subs: React.FC<{phrases: Phrase[]; accent: string}> = ({phrases, accent}) => {
	const fr = useCurrentFrame();
	const {fps, width} = useVideoConfig();
	const t = fr / fps;
	const ph = phrases.find((p) => t >= p.s && t < p.e + 0.12);
	if (!ph) return null;
	const pop = interpolate(fr - Math.round(ph.s * fps), [0, 3], [0.92, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
	return (
		<AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: '18%'}}>
			<div style={{transform: `scale(${pop})`, maxWidth: width * 0.86, textAlign: 'center', fontFamily: MONT, fontWeight: 900,
				fontSize: width * 0.068, lineHeight: 1.1, color: 'white', textTransform: 'uppercase',
				WebkitTextStroke: `${Math.round(width / 360)}px black`, paintOrder: 'stroke fill', textShadow: '0 4px 16px rgba(0,0,0,0.7)'}}>
				{ph.words.map((w, i) => (
					<span key={i} style={{color: t >= w.s && t < w.e + 0.08 ? accent : 'white'}}>{w.t.replace(/[,.]$/, '')} </span>
				))}
			</div>
		</AbsoluteFill>
	);
};

export const Montage: React.FC<MontageProps> = ({fps, segments, phrases, audio, accent}) => {
	let from = 0;
	return (
		<AbsoluteFill style={{background: 'black'}}>
			{segments.map((s, i) => {
				const start = from;
				from += Math.round(s.dur * fps);
				return (
					<Sequence key={i} from={start} durationInFrames={Math.round(s.dur * fps)}>
						{s.kind === 'video'
							? <OffthreadVideo src={staticFile(s.src)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />
							: <Still src={s.src} dur={s.dur} />}
					</Sequence>
				);
			})}
			<Subs phrases={phrases} accent={accent} />
			{audio ? <Audio src={staticFile(audio)} /> : null}
		</AbsoluteFill>
	);
};
