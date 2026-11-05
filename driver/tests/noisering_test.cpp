// User-mode test for the driver's jitter buffer (noisering.h).
// Build and run with driver\tests\run_tests.cmd.

#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "../NoiseMic/noisering.h"

static int g_failures = 0;

#define CHECK(cond)                                                             \
    do {                                                                        \
        if (!(cond)) {                                                          \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);       \
            g_failures++;                                                       \
        }                                                                       \
    } while (0)

static const ULONG kFrame = NOISEMIC_BLOCK_ALIGN;
static const ULONG kWriteBytes = 256 * kFrame;            // one app block (5.33 ms)
static const ULONG kReadBytes = NOISEMIC_BYTES_PER_MS;    // one driver timer tick

// Samples count up so any gap, repeat or reordering shows.
struct Source
{
    SHORT next = 1;
    std::vector<BYTE> Make(ULONG bytes)
    {
        std::vector<BYTE> out(bytes);
        for (ULONG i = 0; i < bytes; i += kFrame)
        {
            SHORT v = next;
            next = next == 32767 ? 1 : next + 1;
            std::memcpy(&out[i], &v, sizeof(v));
        }
        return out;
    }
};

static SHORT SampleAt(const std::vector<BYTE>& data, ULONG frame)
{
    SHORT v;
    std::memcpy(&v, &data[frame * kFrame], sizeof(v));
    return v;
}

static bool AllSilent(const std::vector<BYTE>& data)
{
    for (BYTE b : data)
    {
        if (b != 0) return false;
    }
    return true;
}

static NoiseRing* NewRing()
{
    NoiseRing* ring = static_cast<NoiseRing*>(std::calloc(1, sizeof(NoiseRing)));
    ring->Reset();
    return ring;
}

static void TestSilenceUntilPrimed()
{
    std::printf("silence until primed\n");
    NoiseRing* ring = NewRing();
    Source src;
    std::vector<BYTE> out(kReadBytes);

    ring->Read(out.data(), kReadBytes);
    CHECK(AllSilent(out));
    CHECK(ring->Underruns == 0);

    while (ring->Buffered() + kWriteBytes < NOISEMIC_TARGET_BYTES)
    {
        auto block = src.Make(kWriteBytes);
        ring->Write(block.data(), kWriteBytes);
    }
    ring->Read(out.data(), kReadBytes);
    CHECK(AllSilent(out));
    CHECK(!ring->Primed);

    auto block = src.Make(kWriteBytes);
    ring->Write(block.data(), kWriteBytes);
    ring->Read(out.data(), kReadBytes);
    CHECK(ring->Primed);
    CHECK(!AllSilent(out));
    CHECK(ring->Buffered() == NOISEMIC_TARGET_BYTES - kReadBytes);
    std::free(ring);
}

// Balanced writer and reader for several ring lengths: data must arrive
// complete, in order, with the latency held at the target.
static void TestSteadyStreamWrapsCleanly()
{
    std::printf("steady stream across ring wraps\n");
    NoiseRing* ring = NewRing();
    Source src;
    std::vector<BYTE> out(kReadBytes);
    SHORT expected = 0;
    ULONGLONG written = 0, read = 0;
    ULONG maxBuffered = 0;

    for (int tick = 0; tick < 20000; ++tick)       // 20 s of 1 ms ticks
    {
        // The app delivers 256 frames whenever a block's worth of time has passed.
        while (written < read + NOISEMIC_TARGET_BYTES + kWriteBytes)
        {
            auto block = src.Make(kWriteBytes);
            ring->Write(block.data(), kWriteBytes);
            written += kWriteBytes;
        }
        ring->Read(out.data(), kReadBytes);
        read += kReadBytes;
        if (!ring->Primed) continue;
        for (ULONG f = 0; f < kReadBytes / kFrame; ++f)
        {
            SHORT v = SampleAt(out, f);
            if (expected != 0)
            {
                SHORT want = expected == 32767 ? 1 : expected + 1;
                if (v != want)
                {
                    CHECK(v == want);
                    std::free(ring);
                    return;
                }
            }
            expected = v;
        }
        if (ring->Buffered() > maxBuffered) maxBuffered = ring->Buffered();
    }
    CHECK(ring->Underruns == 0);
    CHECK(ring->Overruns == 0);
    CHECK(maxBuffered <= NOISEMIC_HIGH_BYTES);
    CHECK(read > 2ull * NOISEMIC_RING_BYTES);
    std::free(ring);
}

static void TestUnderrunPadsAndReprimes()
{
    std::printf("underrun pads with silence and re-primes\n");
    NoiseRing* ring = NewRing();
    Source src;
    std::vector<BYTE> out(kReadBytes);

    auto block = src.Make(NOISEMIC_TARGET_BYTES);
    ring->Write(block.data(), NOISEMIC_TARGET_BYTES);
    for (ULONG i = 0; i < NOISEMIC_TARGET_BYTES / kReadBytes; ++i)
    {
        ring->Read(out.data(), kReadBytes);
        CHECK(!AllSilent(out));
    }
    // App stopped: next read finds nothing.
    ring->Read(out.data(), kReadBytes);
    CHECK(AllSilent(out));
    CHECK(ring->Underruns == 1);
    CHECK(!ring->Primed);

    // A short burst is not enough to resume.
    block = src.Make(kWriteBytes);
    ring->Write(block.data(), kWriteBytes);
    ring->Read(out.data(), kReadBytes);
    CHECK(AllSilent(out));
    CHECK(ring->Underruns == 1);
    std::free(ring);
}

static void TestPartialUnderrun()
{
    std::printf("partial underrun keeps the audio it has\n");
    NoiseRing* ring = NewRing();
    Source src;
    auto block = src.Make(NOISEMIC_TARGET_BYTES);
    ring->Write(block.data(), NOISEMIC_TARGET_BYTES);

    std::vector<BYTE> out(NOISEMIC_TARGET_BYTES + 4 * kFrame);
    ring->Read(out.data(), (ULONG)out.size());
    CHECK(SampleAt(out, 0) == 1);
    CHECK(SampleAt(out, NOISEMIC_TARGET_BYTES / kFrame - 1) == (SHORT)(NOISEMIC_TARGET_BYTES / kFrame));
    CHECK(SampleAt(out, NOISEMIC_TARGET_BYTES / kFrame) == 0);
    CHECK(ring->Underruns == 1);
    std::free(ring);
}

static void TestFastWriterIsCapped()
{
    std::printf("fast writer is capped at the high mark\n");
    NoiseRing* ring = NewRing();
    Source src;
    std::vector<BYTE> out(kReadBytes);

    auto block = src.Make(NOISEMIC_TARGET_BYTES);
    ring->Write(block.data(), NOISEMIC_TARGET_BYTES);
    ring->Read(out.data(), kReadBytes);
    CHECK(ring->Primed);

    // Pile up beyond the cap, as a faster input clock would over time.
    while (ring->Buffered() <= NOISEMIC_HIGH_BYTES)
    {
        block = src.Make(kWriteBytes);
        ring->Write(block.data(), kWriteBytes);
    }
    ULONGLONG newest = ring->WritePos;
    ring->Read(out.data(), kReadBytes);
    CHECK(ring->Overruns == 1);
    CHECK(ring->Buffered() == NOISEMIC_TARGET_BYTES - kReadBytes);
    CHECK(ring->ReadPos == newest - NOISEMIC_TARGET_BYTES + kReadBytes);
    std::free(ring);
}

static void TestStalePrefillIsDropped()
{
    std::printf("audio queued while idle is dropped on start\n");
    NoiseRing* ring = NewRing();
    Source src;
    // 200 ms piles up before any capture stream starts.
    auto block = src.Make(200 * NOISEMIC_BYTES_PER_MS);
    ring->Write(block.data(), (ULONG)block.size());

    std::vector<BYTE> out(kReadBytes);
    ring->Read(out.data(), kReadBytes);
    SHORT first = SampleAt(out, 0);
    SHORT want = (SHORT)((200 - 30) * NOISEMIC_BYTES_PER_MS / kFrame + 1);
    CHECK(first == want);
    std::free(ring);
}

static void TestOversizedWriteKeepsTail()
{
    std::printf("oversized write keeps the newest audio\n");
    NoiseRing* ring = NewRing();
    Source src;
    auto block = src.Make(NOISEMIC_RING_BYTES + 64 * kFrame);
    ring->Write(block.data(), (ULONG)block.size());
    CHECK(ring->Buffered() == NOISEMIC_RING_BYTES);

    std::vector<BYTE> out(kReadBytes);
    ring->Read(out.data(), kReadBytes);
    // Priming jumps to the newest TARGET bytes.
    SHORT want = SampleAt(block, (ULONG)(block.size() - NOISEMIC_TARGET_BYTES) / kFrame);
    CHECK(SampleAt(out, 0) == want);
    std::free(ring);
}

int main()
{
    TestSilenceUntilPrimed();
    TestSteadyStreamWrapsCleanly();
    TestUnderrunPadsAndReprimes();
    TestPartialUnderrun();
    TestFastWriterIsCapped();
    TestStalePrefillIsDropped();
    TestOversizedWriteKeepsTail();
    if (g_failures)
    {
        std::printf("%d check(s) failed\n", g_failures);
        return 1;
    }
    std::printf("all noisering tests passed\n");
    return 0;
}
