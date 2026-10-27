ardour {
	["type"]    = "dsp",
	name        = "Sub 808",
	category    = "Instrument",
	license     = "MIT",
	author      = "sound",
	description = [[A mono 808-style sub bass. A clean sine with warm, blended saturation; each fresh
note thumps in with a fast pitch drop; a note that overlaps the one before slides into it (the 808
glide). Drum pads (MIDI channel 10) play the notes of a scale from a root, so every pad is in key;
everything else plays the pitch it's given.]]
}

local SCALES = {
	[0] = { 0, 2, 3, 5, 7, 8, 10 },   -- natural minor
	[1] = { 0, 2, 4, 5, 7, 9, 11 },   -- major
	[2] = { 0, 3, 5, 7, 10 },         -- minor pentatonic
	[3] = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 },  -- chromatic
}
local PAD_CHANNEL = 9   -- "channel 10"

function dsp_params ()
	return {
		{ ["type"] = "input", name = "Root", min = 21, max = 47, default = 30, integer = true,
		  doc = "MIDI note the first pad plays (30 = F#1)" },
		{ ["type"] = "input", name = "Scale", min = 0, max = 3, default = 0, enum = true,
		  scalepoints = { ["Minor"] = 0, ["Major"] = 1, ["Minor pentatonic"] = 2, ["Chromatic"] = 3 } },
		{ ["type"] = "input", name = "First pad", min = 0, max = 112, default = 36, integer = true,
		  doc = "MIDI note the first pad sends" },
		{ ["type"] = "input", name = "Decay", min = 0.1, max = 30, default = 1.1, unit = "s", logarithmic = true },
		{ ["type"] = "input", name = "Release", min = 0.01, max = 1, default = 0.06, unit = "s", logarithmic = true },
		{ ["type"] = "input", name = "Punch", min = 0, max = 36, default = 18, unit = "semitones" },
		{ ["type"] = "input", name = "Glide", min = 10, max = 500, default = 140, unit = "ms" },
		{ ["type"] = "input", name = "Drive", min = 0, max = 1, default = 0.3 },
		{ ["type"] = "input", name = "Gain", min = -30, max = 6, default = -4, unit = "dB" },
		{ ["type"] = "input", name = "Punch time", min = 3, max = 400, default = 12, unit = "ms", logarithmic = true,
		  doc = "How long the punch takes to fall: short is a thump, long is a slow bend down into the note" },
	}
end

function dsp_ioconfig ()
	return { { midi_in = 1, audio_in = 0, audio_out = 2 }, { midi_in = 1, audio_in = 0, audio_out = 1 } }
end

local rate = 48000
local phase = 0
local pitch, target = 30, 30   -- in MIDI note numbers: glides move evenly in pitch, like 808 slides
local level, peak, attacking = 0, 0, false
local held = {}                -- notes currently held, in order
local releasing = true
local bend = 0                 -- punch, in semitones, falling to 0
local lp = 0                   -- smoothing filter state

function dsp_init (r)
	rate = r
end

local function pad_note (n, ctrl)
	local steps = SCALES[math.floor (ctrl[2] + 0.5)] or SCALES[0]
	local i = n - math.floor (ctrl[3] + 0.5)
	if i < 0 then return n end
	return math.floor (ctrl[1] + 0.5) + 12 * (i // #steps) + steps[i % #steps + 1]
end

local function sat (x)   -- tanh-shaped
	if x > 3 then return 1 elseif x < -3 then return -1 end
	return x * (27 + x * x) / (27 + 9 * x * x)
end

local function forget (n)
	for i = #held, 1, -1 do if held[i] == n then table.remove (held, i) end end
end

function dsp_run (ins, outs, n_samples)
	local ctrl = CtrlPorts:array ()
	local decay = math.exp (math.log (0.001) / (ctrl[4] * rate))      -- -60 dB over Decay
	local release = math.exp (math.log (0.001) / (ctrl[5] * rate))
	local punch_fall = math.exp (-1 / (ctrl[10] * 0.001 * rate))       -- default 12 ms: a thump, not a zap
	local glide = 1 - math.exp (-1 / (ctrl[7] * 0.001 * rate / 4))      -- ~Glide ms to arrive
	local attack = 1 - math.exp (-1 / (0.001 * rate))
	local drive = ctrl[8]
	local k = 1 + 4 * drive
	local norm = 1 / sat (k)
	local smooth = 1 - math.exp (-2 * math.pi * 1800 / rate)           -- soften the added harmonics
	local gain = ARDOUR.DSP.dB_to_coefficient (ctrl[9])
	local a = {}

	local function render (from, to)
		for s = from, to do
			if attacking then
				level = level + (peak - level) * attack
				if level >= peak * 0.995 then attacking = false end
			else
				level = level * (releasing and release or decay)
			end
			pitch = pitch + (target - pitch) * glide
			bend = bend * punch_fall
			phase = phase + 440 * 2 ^ ((pitch + bend - 69) / 12) / rate
			if phase >= 1 then phase = phase - 1 end
			local clean = math.sin (6.283185307179586 * phase)
			local warm = sat (k * clean) * norm
			lp = lp + (warm - lp) * smooth
			a[s] = gain * level * (clean + drive * (lp - clean))
		end
	end

	local t0 = 1
	for _, ev in ipairs (midiin) do
		local t = ev["time"]
		render (t0, t - 1)
		t0 = t
		local d = ev["data"]
		local kind, channel = #d == 3 and (d[1] & 0xF0) or 0, d[1] & 0x0F
		if kind == 0x90 and d[3] > 0 then
			local note = channel == PAD_CHANNEL and pad_note (d[2], ctrl) or d[2]
			if #held > 0 then
				target = note                         -- overlapping the last note: slide
			else
				pitch, target, bend = note, note, ctrl[6]   -- a fresh hit: thump in
				peak = (d[3] / 127) ^ 0.5
				attacking = true
			end
			if #held > 0 then peak = math.max (peak, (d[3] / 127) ^ 0.5) attacking = true end
			forget (d[2])
			held[#held + 1] = d[2]
			releasing = false
		elseif kind == 0x80 or (kind == 0x90 and d[3] == 0) then
			forget (d[2])
			if #held == 0 then releasing = true end
		elseif kind == 0xB0 and (d[2] == 120 or d[2] == 123) then
			level, attacking, releasing, held = 0, false, true, {}
		end
	end
	render (t0, n_samples)

	for c = 1, #outs do
		outs[c]:set_table (a, n_samples)
	end
end
