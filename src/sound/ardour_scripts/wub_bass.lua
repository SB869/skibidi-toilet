ardour {
	["type"]    = "dsp",
	name        = "Wub Bass",
	category    = "Instrument",
	license     = "MIT",
	author      = "sound",
	description = [[A dubstep wobble bass: detuned saws and a square, a frequency-modulated growl at an
inharmonic ratio for grit and dissonance, a resonant low-pass swept by a tempo-synced LFO (the
wub), drive before and after the filter, and a high-pass so the sub stays with the 808.
MIDI CC 16 picks the wub rate (1/2 1/4 1/4T 1/8 1/8T 1/16 1/16T 1/32, in steps of 16);
CC 17 sets the growl. A note overlapping the last one slides into it.]]
}

local RATES = { 2, 1, 2 / 3, 0.5, 1 / 3, 0.25, 1 / 6, 0.125 }   -- beats per wub

function dsp_params ()
	return {
		{ ["type"] = "input", name = "BPM", min = 40, max = 200, default = 92 },
		{ ["type"] = "input", name = "Rate", min = 0, max = 7, default = 3, enum = true,
		  scalepoints = { ["1/2"] = 0, ["1/4"] = 1, ["1/4T"] = 2, ["1/8"] = 3, ["1/8T"] = 4, ["1/16"] = 5, ["1/16T"] = 6, ["1/32"] = 7 } },
		{ ["type"] = "input", name = "Growl", min = 0, max = 1, default = 0.35 },
		{ ["type"] = "input", name = "Low cutoff", min = 40, max = 1000, default = 140, unit = "Hz", logarithmic = true },
		{ ["type"] = "input", name = "High cutoff", min = 300, max = 8000, default = 2600, unit = "Hz", logarithmic = true },
		{ ["type"] = "input", name = "Resonance", min = 0, max = 0.95, default = 0.72 },
		{ ["type"] = "input", name = "Drive", min = 0, max = 1, default = 0.6 },
		{ ["type"] = "input", name = "High-pass", min = 20, max = 400, default = 110, unit = "Hz", logarithmic = true },
		{ ["type"] = "input", name = "Glide", min = 1, max = 300, default = 40, unit = "ms" },
		{ ["type"] = "input", name = "Gain", min = -30, max = 6, default = -9, unit = "dB" },
	}
end

function dsp_ioconfig ()
	return { { midi_in = 1, audio_in = 0, audio_out = 2 }, { midi_in = 1, audio_in = 0, audio_out = 1 } }
end

local rate = 48000
local p1, p2, p3, pg, pm, lfo = 0, 0.33, 0.66, 0, 0, 0
local pitch, target = 42, 42
local level, peak, gate = 0, 0, false
local held = {}
local cc_rate, cc_growl = nil, nil
local l1, l2, h1, h2, count = 0, 0, 0, 0, 0
local a1, a2, a3 = 1, 0, 0

function dsp_init (r)
	rate = r
end

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

local function sat (x)
	if x > 3 then return 1 elseif x < -3 then return -1 end
	return x * (27 + x * x) / (27 + 9 * x * x)
end

local function forget (n)
	for i = #held, 1, -1 do if held[i] == n then table.remove (held, i) end end
end

function dsp_run (ins, outs, n_samples)
	local c = CtrlPorts:array ()
	local beats_per_wub = RATES[(cc_rate or math.floor (c[2] + 0.5)) + 1] or 0.5
	local lfo_inc = (c[1] / 60) / beats_per_wub / rate
	local growl = cc_growl or c[3]
	local lo, hi, k = c[4], c[5], 2 - 2 * c[6]
	local drive = 1 + 5 * c[7]
	local dnorm = 1 / sat (drive)
	local hp = 1 - math.exp (-2 * math.pi * c[8] / rate)
	local glide = 1 - math.exp (-1 / (c[9] * 0.001 * rate / 4))
	local gain = ARDOUR.DSP.dB_to_coefficient (c[10])
	local attack, release = 1 - math.exp (-1 / (0.003 * rate)), math.exp (math.log (0.001) / (0.06 * rate))
	local ratio = hi / lo
	local a = {}

	local function render (from, to)
		for s = from, to do
			if gate then level = level + (peak - level) * attack else level = level * release end
			pitch = pitch + (target - pitch) * glide
			local f = 440 * 2 ^ ((pitch - 69) / 12) / rate
			-- oscillators: two detuned saws, a square, and the FM growl (inharmonic ratio: dissonant, metallic)
			local d1, d2 = f * 1.006, f * 0.994
			p1 = p1 + d1; if p1 >= 1 then p1 = p1 - 1 end
			p2 = p2 + d2; if p2 >= 1 then p2 = p2 - 1 end
			p3 = p3 + f;  if p3 >= 1 then p3 = p3 - 1 end
			pg = pg + f;  if pg >= 1 then pg = pg - 1 end
			pm = pm + f * 1.414; if pm >= 1 then pm = pm - 1 end
			local saw = (2 * p1 - 1 - blep (p1, d1)) + (2 * p2 - 1 - blep (p2, d2))
			local q = p3 + 0.5; if q >= 1 then q = q - 1 end
			local square = (p3 < 0.5 and 1 or -1) + blep (p3, f) - blep (q, f)
			local fm = math.sin (6.283185307179586 * pg + 3 * growl * math.sin (6.283185307179586 * pm))
			local x = sat (drive * (0.35 * saw + 0.3 * square + growl * 0.9 * fm)) * dnorm
			-- the wub: a tempo-synced sweep of a resonant low-pass, closed at the start of each cycle
			lfo = lfo + lfo_inc; if lfo >= 1 then lfo = lfo - 1 end
			if count == 0 then
				local wub = 0.5 - 0.5 * math.cos (6.283185307179586 * lfo)
				local fc = lo * ratio ^ wub
				if fc > 0.45 * rate then fc = 0.45 * rate end
				local g = math.tan (math.pi * fc / rate)
				a1 = 1 / (1 + g * (g + k)); a2 = g * a1; a3 = g * a2
			end
			count = (count + 1) % 16
			local v3 = x - l2
			local v1 = a1 * l1 + a2 * v3
			local v2 = l2 + a2 * l1 + a3 * v3
			l1, l2 = 2 * v1 - l1, 2 * v2 - l2
			local y = sat (1.6 * v2)
			-- leave the sub to the 808: two one-pole high-passes
			h1 = h1 + (y - h1) * hp
			local y1 = y - h1
			h2 = h2 + (y1 - h2) * hp
			a[s] = gain * level * (y1 - h2)
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
			if kind == 0x90 and d[3] > 0 then
				if #held > 0 then
					target = d[2]                                -- overlapping: slide
				else
					pitch, target, lfo, count = d[2], d[2], 0, 0  -- a fresh hit: the wub starts closed
				end
				peak, gate = (d[3] / 127) ^ 0.6, true
				forget (d[2]); held[#held + 1] = d[2]
			elseif kind == 0x80 or kind == 0x90 then
				forget (d[2])
				if #held == 0 then gate = false end
			elseif kind == 0xB0 then
				if d[2] == 16 then cc_rate = math.min (7, d[3] // 16)
				elseif d[2] == 17 then cc_growl = d[3] / 127
				elseif d[2] == 120 or d[2] == 123 then held, gate, level = {}, false, 0 end
			end
		end
	end
	render (t0, n_samples)

	for ch = 1, #outs do
		outs[ch]:set_table (a, n_samples)
	end
end
