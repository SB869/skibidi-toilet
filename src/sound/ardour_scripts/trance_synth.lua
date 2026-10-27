ardour {
	["type"]    = "dsp",
	name        = "Trance Synth",
	category    = "Instrument",
	license     = "MIT",
	author      = "sound",
	description = [[A polyphonic subtractive synth for leads, pads and bass: up to seven detuned saws or
pulses per note (a supersaw), a resonant low-pass filter with its own envelope that velocity
opens further, an ADSR, and stereo width from spreading the unison voices.]]
}

function dsp_params ()
	return {
		{ ["type"] = "input", name = "Wave", min = 0, max = 2, default = 0, enum = true,
		  scalepoints = { ["Saw"] = 0, ["Square"] = 1, ["Sine"] = 2 } },
		{ ["type"] = "input", name = "Unison", min = 1, max = 7, default = 5, integer = true },
		{ ["type"] = "input", name = "Detune", min = 0, max = 60, default = 18, unit = "cents" },
		{ ["type"] = "input", name = "Octave", min = -3, max = 2, default = 0, integer = true },
		{ ["type"] = "input", name = "Cutoff", min = 60, max = 16000, default = 1800, unit = "Hz", logarithmic = true },
		{ ["type"] = "input", name = "Resonance", min = 0, max = 0.95, default = 0.3 },
		{ ["type"] = "input", name = "Filter env", min = 0, max = 6, default = 2.5, unit = "octaves" },
		{ ["type"] = "input", name = "Filter decay", min = 0.01, max = 4, default = 0.25, unit = "s", logarithmic = true },
		{ ["type"] = "input", name = "Velocity", min = 0, max = 1, default = 0.6, doc = "how far velocity opens the filter" },
		{ ["type"] = "input", name = "Attack", min = 0.001, max = 4, default = 0.003, unit = "s", logarithmic = true },
		{ ["type"] = "input", name = "Decay", min = 0.01, max = 6, default = 0.3, unit = "s", logarithmic = true },
		{ ["type"] = "input", name = "Sustain", min = 0, max = 1, default = 0.6 },
		{ ["type"] = "input", name = "Release", min = 0.005, max = 6, default = 0.25, unit = "s", logarithmic = true },
		{ ["type"] = "input", name = "Width", min = 0, max = 1, default = 0.7 },
		{ ["type"] = "input", name = "Gain", min = -30, max = 6, default = -10, unit = "dB" },
	}
end

function dsp_ioconfig ()
	return { { midi_in = 1, audio_in = 0, audio_out = 2 } }
end

local MAX_VOICES, MAX_UNISON, CONTROL_RATE = 8, 7, 16
local rate = 48000
local voices = {}
local age = 0

function dsp_init (r)
	rate = r
	for i = 1, MAX_VOICES do
		local v = { active = false, note = -1, gate = false, env = 0, stage = 0, fenv = 0, vel = 0, age = 0,
		            phase = {}, l1 = 0, l2 = 0, r1 = 0, r2 = 0, a1 = 1, a2 = 0, a3 = 0, count = 0 }
		for u = 1, MAX_UNISON do v.phase[u] = 0 end
		voices[i] = v
	end
end

-- band-limited corrections, so the saws and pulses don't alias into fizz
local function blep (t, dt)
	if t < dt then
		local x = t / dt
		return x + x - x * x - 1
	elseif t > 1 - dt then
		local x = (t - 1) / dt
		return x * x + x + x + 1
	end
	return 0
end

local function note_on (note, vel)
	local pick
	for _, v in ipairs (voices) do
		if v.active and v.note == note then pick = v break end
	end
	if not pick then
		for _, v in ipairs (voices) do if not v.active then pick = v break end end
	end
	if not pick then   -- steal the oldest
		pick = voices[1]
		for _, v in ipairs (voices) do if v.age < pick.age then pick = v end end
	end
	age = age + 1
	pick.active, pick.note, pick.gate, pick.stage, pick.vel, pick.age = true, note, true, 1, vel / 127, age
	pick.fenv, pick.count = 1, 0
	if pick.env == 0 then
		for u = 1, MAX_UNISON do pick.phase[u] = math.random () end   -- free-running unison: width
		pick.l1, pick.l2, pick.r1, pick.r2 = 0, 0, 0, 0
	end
end

local function note_off (note)
	for _, v in ipairs (voices) do
		if v.active and v.note == note and v.gate then v.gate, v.stage = false, 4 end
	end
end

function dsp_run (ins, outs, n_samples)
	local c = CtrlPorts:array ()
	local wave, unison, detune, octave = math.floor (c[1] + 0.5), math.floor (c[2] + 0.5), c[3], math.floor (c[4] + 0.5)
	local cutoff, k = c[5], 2 - 2 * c[6]
	local fenv_amt, fdecay = c[7], math.exp (-1 / (c[8] * rate))
	local vel_amt = c[9]
	local attack = 1 / (c[10] * rate)
	local decay, sustain = math.exp (math.log (0.001) / (c[11] * rate)), c[12]
	local release = math.exp (math.log (0.001) / (c[13] * rate))
	local width, gain = c[14], ARDOUR.DSP.dB_to_coefficient (c[15]) / math.sqrt (unison)
	local nyquist = 0.45 * rate

	-- unison spread: detune (as a frequency ratio) and pan per voice
	local ratio, panl, panr = {}, {}, {}
	for u = 1, unison do
		local pos = unison > 1 and ((u - 1) / (unison - 1) * 2 - 1) or 0
		ratio[u] = 2 ^ (detune * pos / 1200)
		panl[u], panr[u] = 0.5 - 0.5 * width * pos, 0.5 + 0.5 * width * pos
	end

	local L, R = {}, {}
	for s = 1, n_samples do L[s], R[s] = 0, 0 end

	local function render (from, to)
		for _, v in ipairs (voices) do
			if v.active then
				local base = 440 * 2 ^ ((v.note + 12 * octave - 69) / 12) / rate
				local ph = v.phase
				for s = from, to do
					-- amplitude envelope
					if v.stage == 1 then
						v.env = v.env + attack
						if v.env >= 1 then v.env, v.stage = 1, 2 end
					elseif v.stage == 2 then
						v.env = sustain + (v.env - sustain) * decay
					elseif v.stage == 4 then
						v.env = v.env * release
						if v.env < 1e-4 then v.env, v.active, v.stage = 0, false, 0 break end
					end
					v.fenv = v.fenv * fdecay
					-- filter coefficients at control rate
					if v.count == 0 then
						local fc = cutoff * 2 ^ (fenv_amt * v.fenv + vel_amt * 2 * (v.vel - 0.5))
						if fc > nyquist then fc = nyquist end
						local g = math.tan (math.pi * fc / rate)
						v.a1 = 1 / (1 + g * (g + k)); v.a2 = g * v.a1; v.a3 = g * v.a2
					end
					v.count = (v.count + 1) % CONTROL_RATE
					-- oscillators
					local sl, sr = 0, 0
					for u = 1, unison do
						local dt = base * ratio[u]
						local t = ph[u] + dt
						if t >= 1 then t = t - 1 end
						ph[u] = t
						local y
						if wave == 0 then
							y = 2 * t - 1 - blep (t, dt)
						elseif wave == 1 then
							local t2 = t + 0.5
							if t2 >= 1 then t2 = t2 - 1 end
							y = (t < 0.5 and 1 or -1) + blep (t, dt) - blep (t2, dt)
						else
							y = math.sin (6.283185307179586 * t)
						end
						sl = sl + y * panl[u]
						sr = sr + y * panr[u]
					end
					-- resonant low-pass (state-variable, trapezoidal), one per side
					local a1, a2, a3 = v.a1, v.a2, v.a3
					local v3 = sl - v.l2
					local v1 = a1 * v.l1 + a2 * v3
					local v2 = v.l2 + a2 * v.l1 + a3 * v3
					v.l1, v.l2 = 2 * v1 - v.l1, 2 * v2 - v.l2
					v3 = sr - v.r2
					local w1 = a1 * v.r1 + a2 * v3
					local w2 = v.r2 + a2 * v.r1 + a3 * v3
					v.r1, v.r2 = 2 * w1 - v.r1, 2 * w2 - v.r2
					local amp = v.env * gain * (0.4 + 0.6 * v.vel)
					L[s] = L[s] + v2 * amp
					R[s] = R[s] + w2 * amp
				end
			end
		end
	end

	local t0 = 1
	for _, ev in ipairs (midiin) do
		local t = ev["time"]
		render (t0, t - 1)
		t0 = t
		local d = ev["data"]
		if #d == 3 then
			local kind = d[1] & 0xF0
			if kind == 0x90 and d[3] > 0 then note_on (d[2], d[3])
			elseif kind == 0x80 or kind == 0x90 then note_off (d[2])
			elseif kind == 0xB0 and (d[2] == 120 or d[2] == 123) then
				for _, v in ipairs (voices) do v.active, v.env, v.stage = false, 0, 0 end
			end
		end
	end
	render (t0, n_samples)

	outs[1]:set_table (L, n_samples)
	if #outs > 1 then outs[2]:set_table (R, n_samples) end
end
