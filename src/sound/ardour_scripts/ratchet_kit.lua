ardour {
	["type"]    = "dsp",
	name        = "Ratchet Kit",
	category    = "Instrument",
	license     = "MIT",
	author      = "sound",
	description = [[A small synthesized drum machine for West Coast bounce: claps, finger snaps, tight
hats, a punchy kick and a snare, on the General MIDI drum notes (kick 36, snap 37, snare 38,
clap 39, closed hat 42, pedal hat 44, open hat 46).]]
}

function dsp_params ()
	return {
		{ ["type"] = "input", name = "Clap tone", min = 600, max = 2500, default = 1150, unit = "Hz", logarithmic = true },
		{ ["type"] = "input", name = "Hat tone", min = 4000, max = 12000, default = 8000, unit = "Hz", logarithmic = true },
		{ ["type"] = "input", name = "Kick level", min = -30, max = 6, default = -4, unit = "dB" },
		{ ["type"] = "input", name = "Gain", min = -30, max = 6, default = -3, unit = "dB" },
	}
end

function dsp_ioconfig ()
	return { { midi_in = 1, audio_in = 0, audio_out = 2 }, { midi_in = 1, audio_in = 0, audio_out = 1 } }
end

local rate = 48000
local voices = {}
local CLAP_BURSTS = { 0, 9, 18 }   -- ms: three hands, slightly apart (no table built per sample)

function dsp_init (r)
	rate = r
end

-- RBJ biquads
local function bandpass (f, q)
	local w = 2 * math.pi * f / rate
	local alpha = math.sin (w) / (2 * q)
	local a0 = 1 + alpha
	return { alpha / a0, 0, -alpha / a0, -2 * math.cos (w) / a0, (1 - alpha) / a0, 0, 0, 0, 0 }
end

local function highpass (f, q)
	local w = 2 * math.pi * f / rate
	local c, alpha = math.cos (w), math.sin (w) / (2 * q)
	local a0 = 1 + alpha
	return { (1 + c) / 2 / a0, -(1 + c) / a0, (1 + c) / 2 / a0, -2 * c / a0, (1 - alpha) / a0, 0, 0, 0, 0 }
end

local function filter (b, x)
	local y = b[1] * x + b[2] * b[6] + b[3] * b[7] - b[4] * b[8] - b[5] * b[9]
	b[7], b[6] = b[6], x
	b[9], b[8] = b[8], y
	return y
end

local function noise () return 2 * math.random () - 1 end

local function trigger (note, vel, ctrl)
	local v = { t = 0, vel = (vel / 127) ^ 0.8 }
	local ms = rate / 1000
	if note == 36 then
		v.kind = "kick"; v.phase = 0; v.len = 0.5 * rate
		v.vel = v.vel * ARDOUR.DSP.dB_to_coefficient (ctrl[3])
	elseif note == 37 then
		v.kind = "snap"; v.f = bandpass (2400, 1.6); v.len = 0.12 * rate
	elseif note == 38 then
		v.kind = "snare"; v.f = bandpass (1800, 0.8); v.phase = 0; v.len = 0.3 * rate
	elseif note == 39 then
		v.kind = "clap"; v.f = bandpass (ctrl[1], 1.2); v.len = 0.35 * rate
	elseif note == 42 or note == 44 then
		v.kind = "hat"; v.f = highpass (ctrl[2], 0.7); v.tau = 12 * ms; v.len = 0.08 * rate
		if note == 44 then v.vel = v.vel * 0.6 end
		voices[46] = nil   -- a closed hat chokes the open one
	elseif note == 46 then
		v.kind = "hat"; v.f = highpass (ctrl[2] * 0.9, 0.7); v.tau = 90 * ms; v.len = 0.6 * rate
	else
		return
	end
	voices[note] = v
end

local function sample (v)
	local t, ms = v.t, rate / 1000
	local x = 0
	if v.kind == "kick" then
		local f = 48 + 102 * math.exp (-t / (30 * ms))
		v.phase = v.phase + f / rate
		x = math.sin (2 * math.pi * v.phase) * math.exp (-t / (110 * ms))
		if t < 2 * ms then x = x + 0.3 * noise () end
	elseif v.kind == "snap" then
		x = 2.2 * filter (v.f, noise ()) * math.exp (-t / (15 * ms))
		  + 0.4 * math.sin (2 * math.pi * 1600 * t / rate) * math.exp (-t / (8 * ms))
	elseif v.kind == "snare" then
		v.phase = v.phase + 185 / rate
		x = 1.6 * filter (v.f, noise ()) * math.exp (-t / (45 * ms))
		  + 0.5 * math.sin (2 * math.pi * v.phase) * math.exp (-t / (30 * ms))
	elseif v.kind == "clap" then
		local env = 0
		for _, at in ipairs (CLAP_BURSTS) do
			local d = t - at * ms
			if d >= 0 then env = math.max (env, math.exp (-d / (2.5 * ms))) end
		end
		local d = t - 24 * ms
		if d >= 0 then env = math.max (env, 0.8 * math.exp (-d / (55 * ms))) end
		x = 2.6 * filter (v.f, noise ()) * env
	elseif v.kind == "hat" then
		x = 0.9 * filter (v.f, noise ()) * math.exp (-t / v.tau)
	end
	v.t = t + 1
	return x * v.vel
end

function dsp_run (ins, outs, n_samples)
	local ctrl = CtrlPorts:array ()
	local gain = ARDOUR.DSP.dB_to_coefficient (ctrl[4])
	local a = {}

	local function render (from, to)
		for s = from, to do
			local mix = 0
			for note, v in pairs (voices) do
				mix = mix + sample (v)
				if v.t > v.len then voices[note] = nil end
			end
			a[s] = gain * mix
		end
	end

	local t0 = 1
	for _, ev in ipairs (midiin) do
		local t = ev["time"]
		render (t0, t - 1)
		t0 = t
		local d = ev["data"]
		if #d == 3 and (d[1] & 0xF0) == 0x90 and d[3] > 0 then trigger (d[2], d[3], ctrl) end
	end
	render (t0, n_samples)

	for c = 1, #outs do
		outs[c]:set_table (a, n_samples)
	end
end
