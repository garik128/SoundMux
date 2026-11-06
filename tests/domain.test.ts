import { test } from 'node:test';
import assert from 'node:assert/strict';
import { jobReducer, initialJob } from '../src/domain/jobReducer.ts';
import { formatRemaining, initialAdjustment, totalDelay } from '../src/domain/types.ts';
import { mediaService, wait } from '../src/services/mockMediaService.ts';
import { demoSources, demoResult } from '../src/fixtures/scenarios.ts';
import { durationMismatch, formatClock } from '../src/domain/durations.ts';
import { defaultTrackTitle, factoryOutputDefaults, outputDefaultsVersion, parseOutputDefaults, serializeOutputDefaults, settingsFromDefaults } from '../src/app/useOutputDefaults.ts';
test('cancelled and replaced jobs ignore late success', () => {
  const running = jobReducer(initialJob, { type: 'start', taskId: 1, status: 'analyzing' });
  const cancelled = jobReducer(running, { type: 'finish', taskId: 1, status: 'cancelled' });
  assert.equal(jobReducer(cancelled, { type: 'finish', taskId: 1, status: 'review', result: demoResult('offset') }), cancelled);
  const replaced = jobReducer(running, { type: 'reset', taskId: 2, ready: true });
  assert.equal(jobReducer(replaced, { type: 'finish', taskId: 1, status: 'review', result: demoResult('offset') }), replaced);
});
test('positive delay plays later and manual reset preserves automatic correction', () => {
  assert.equal(totalDelay({ ...initialAdjustment, automaticDelayMs: 1400, manualDelayMs: 50 }), 1450);
  assert.equal(totalDelay({ ...initialAdjustment, automaticDelayMs: 1400 }), 1400);
});
test('abort stops timer and prevents completion', async () => {
  const controller = new AbortController(); const promise = wait(100, controller.signal); controller.abort();
  await assert.rejects(promise, { name: 'AbortError' });
});
test('build blocks unverified sound without explicit acknowledgement', async () => {
  await assert.rejects(mediaService.build({ ...demoSources(), scenario: 'uncertain', settings: {} as never, adjustment: initialAdjustment, outputName: 'test.NEW.mkv', allowUnverified: false }, new AbortController().signal, () => {}), /Подтвердите/);
});
test('save failures retain analysis and return directly to saving', () => {
  const result = demoResult('offset');
  const review = { status: 'review' as const, taskId: 1, result };
  const running = jobReducer(review, { type: 'start', taskId: 2, status: 'building' });
  const failed = jobReducer(running, { type: 'failure', taskId: 2, error: 'Output exists' });
  assert.equal(failed.status, 'review');
  assert.equal(failed.result, result);
  assert.equal(jobReducer(failed, { type: 'finish', taskId: 2, status: 'completed' }), failed);
  const retry = jobReducer(failed, { type: 'start', taskId: 3, status: 'building' });
  assert.equal(retry.result, result);
  assert.equal(retry.error, undefined);
});
test('a new analysis never retains an older successful result after failure', () => {
  const running = jobReducer({ status: 'review', taskId: 1, result: demoResult('offset') }, { type: 'start', taskId: 2, status: 'analyzing' });
  const failed = jobReducer(running, { type: 'failure', taskId: 2, error: 'Read failed' });
  assert.equal(failed.status, 'error');
  assert.equal(failed.result, undefined);
});
test('remaining time is hidden when short and rounded to minutes otherwise', () => {
  assert.equal(formatRemaining(null), '');
  assert.equal(formatRemaining(12), '');
  assert.equal(formatRemaining(60), 'осталось около минуты');
  assert.equal(formatRemaining(222), 'осталось около 4 мин');
});
test('remembered defaults accept only valid values and fall back per field', () => {
  assert.deepEqual(parseOutputDefaults(null), factoryOutputDefaults);
  assert.deepEqual(parseOutputDefaults('{ broken'), factoryOutputDefaults);
  assert.deepEqual(parseOutputDefaults('[1,2]'), factoryOutputDefaults);
  const mixed = parseOutputDefaults(JSON.stringify({ v: outputDefaultsVersion, language: 'Английский', title: '  ', makeDefault: 'yes', keepOriginal: false, compatibility: true }));
  assert.equal(mixed.language, 'Английский');
  assert.equal(mixed.title, factoryOutputDefaults.title);
  assert.equal(mixed.makeDefault, true);
  assert.equal(mixed.keepOriginal, false);
  assert.equal(mixed.compatibility, true);
  assert.equal(parseOutputDefaults(JSON.stringify({ v: outputDefaultsVersion, language: 'Klingon' })).language, 'Русский');
  assert.equal(parseOutputDefaults(JSON.stringify({ v: outputDefaultsVersion, title: 'x'.repeat(200) })).title.length, 80);
});
test('track title comes from the dub file name only when asked and available', () => {
  const custom = { title: 'Русская озвучка', titleFromFile: false };
  const fromFile = { title: 'Русская озвучка', titleFromFile: true };
  assert.equal(defaultTrackTitle(custom, 'Фильм.Дубляж [HDRezka].eac3'), 'Русская озвучка');
  assert.equal(defaultTrackTitle(fromFile, 'Фильм.Дубляж [HDRezka].eac3'), 'Фильм.Дубляж [HDRezka]');
  assert.equal(defaultTrackTitle(fromFile, 'без расширения'), 'без расширения');
  assert.equal(defaultTrackTitle(fromFile, undefined), 'Русская озвучка');
  assert.equal(defaultTrackTitle(fromFile, '.eac3'), 'Русская озвучка');
  assert.equal(defaultTrackTitle(fromFile, 'x'.repeat(300) + '.ac3').length, 120);
  assert.equal(factoryOutputDefaults.titleFromFile, true, 'the dub file name is the default track title');
  assert.equal(parseOutputDefaults(null).titleFromFile, true);
  assert.equal(parseOutputDefaults('{"v":2,"titleFromFile":false}').titleFromFile, false);
  assert.equal(parseOutputDefaults('{"v":2,"titleFromFile":"yes"}').titleFromFile, true, 'a malformed value falls back to the factory default');
  assert.equal('titleFromFile' in settingsFromDefaults(parseOutputDefaults(null)), false);
});
test('stored defaults are versioned and keep only what the user changed', () => {
  assert.equal(serializeOutputDefaults(factoryOutputDefaults), null);
  const stored = serializeOutputDefaults({ ...factoryOutputDefaults, language: 'Английский' });
  assert.deepEqual(JSON.parse(stored!), { v: outputDefaultsVersion, language: 'Английский' });
  assert.equal(parseOutputDefaults(stored).language, 'Английский');
  assert.equal(parseOutputDefaults(stored).titleFromFile, factoryOutputDefaults.titleFromFile);
  // Values written by earlier builds carry no version and must not pin any setting.
  assert.deepEqual(parseOutputDefaults('{"language":"Английский","titleFromFile":false,"title":"Старое"}'), factoryOutputDefaults);
});
test('a large difference in length warns, expected conversions and small differences do not', () => {
  const movie = 124 * 60;
  assert.deepEqual(durationMismatch(movie, 139 * 60), { movie, dub: 139 * 60, difference: 15 * 60 });
  assert.equal(durationMismatch(movie, movie * (25 / (24000 / 1001))), null, 'PAL speed-up is a known conversion');
  assert.equal(durationMismatch(movie, movie / (25 / 24)), null, 'slow-down to 24 fps is a known conversion');
  assert.equal(durationMismatch(movie, movie + 90), null, 'a minute and a half is normal (credits, logos)');
  assert.notEqual(durationMismatch(60 * 60, 60 * 60 + 400), null, 'almost seven extra minutes on a one-hour film is a lot');
  assert.equal(durationMismatch(60 * 60, 60 * 60 + 100), null, 'under two minutes never warns');
  assert.equal(durationMismatch(0, 100), null);
  assert.equal(durationMismatch(undefined, 100), null);
  assert.equal(formatClock(124 * 60), '02:04:00');
  assert.equal(formatClock(15 * 60), '00:15:00');
  assert.equal(formatClock(40), '00:00:40');
  assert.equal(formatClock(5974.976), '01:39:35', 'rounded to the nearest second, like the site');
  assert.equal(formatClock(59.6), '00:01:00');
  assert.equal(formatClock(-3), '00:00:00');
});
