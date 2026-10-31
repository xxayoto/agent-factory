import {Composition} from 'remotion';
import {Montage, MontageProps} from './Montage';

// Размер и длительность приходят из props (factory/agents/montage.py пишет props.json)
const defaults: MontageProps = {fps: 25, width: 1080, height: 1440, segments: [], phrases: [], audio: null, accent: '#FFD54A'};

export const Root = () => (
	<Composition
		id="Montage"
		component={Montage}
		defaultProps={defaults}
		fps={25}
		width={1080}
		height={1440}
		durationInFrames={1}
		calculateMetadata={({props}) => ({
			fps: props.fps,
			width: props.width,
			height: props.height,
			durationInFrames: Math.max(1, Math.round(props.segments.reduce((a, s) => a + s.dur, 0) * props.fps)),
		})}
	/>
);
