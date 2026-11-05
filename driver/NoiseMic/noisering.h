/*++

Module Name:

    noisering.h

Abstract:

    Jitter buffer between the app's writes and the capture stream's reads.
    It has no kernel dependencies so the user-mode test can include it.
    The caller provides locking.

--*/

#ifndef _NOISEMIC_NOISERING_H_
#define _NOISEMIC_NOISERING_H_

#include "noisemic_shared.h"

#define NOISEMIC_RING_BYTES             (1u << 16)                      // ~680 ms
#define NOISEMIC_TARGET_BYTES           (30 * NOISEMIC_BYTES_PER_MS)    // latency kept after (re)start
#define NOISEMIC_HIGH_BYTES             (90 * NOISEMIC_BYTES_PER_MS)    // latency cap before dropping

static_assert(NOISEMIC_TARGET_BYTES % NOISEMIC_BLOCK_ALIGN == 0, "target must hold whole frames");
static_assert(NOISEMIC_HIGH_BYTES % NOISEMIC_BLOCK_ALIGN == 0, "cap must hold whole frames");
static_assert(NOISEMIC_RING_BYTES % NOISEMIC_BLOCK_ALIGN == 0, "ring must hold whole frames");
static_assert(NOISEMIC_HIGH_BYTES < NOISEMIC_RING_BYTES, "cap must fit in the ring");

struct NoiseRing
{
    BYTE        Data[NOISEMIC_RING_BYTES];
    ULONGLONG   WritePos;       // Total bytes ever written.
    ULONGLONG   ReadPos;        // Total bytes consumed or skipped.
    BOOLEAN     Primed;         // FALSE until TARGET bytes are waiting.
    ULONG       Underruns;
    ULONG       Overruns;

    void Reset()
    {
        WritePos = 0;
        ReadPos = 0;
        Primed = FALSE;
        Underruns = 0;
        Overruns = 0;
    }

    ULONG Buffered() const
    {
        return (ULONG)(WritePos - ReadPos);
    }

    // Callers pass whole frames, so positions stay frame aligned.
    void Write(const BYTE* Source, ULONG Length)
    {
        if (Length > NOISEMIC_RING_BYTES)
        {
            Source += Length - NOISEMIC_RING_BYTES;
            Length = NOISEMIC_RING_BYTES;
        }

        ULONG offset = (ULONG)(WritePos % NOISEMIC_RING_BYTES);
        ULONG first = Length < NOISEMIC_RING_BYTES - offset ? Length : NOISEMIC_RING_BYTES - offset;
        RtlCopyMemory(Data + offset, Source, first);
        RtlCopyMemory(Data, Source + first, Length - first);
        WritePos += Length;

        if (WritePos - ReadPos > NOISEMIC_RING_BYTES)
        {
            ReadPos = WritePos - NOISEMIC_RING_BYTES;
        }
    }

    // Always fills Length bytes; silence covers any gap.
    void Read(BYTE* Destination, ULONG Length)
    {
        ULONGLONG available = WritePos - ReadPos;

        if (!Primed)
        {
            if (available < NOISEMIC_TARGET_BYTES)
            {
                RtlZeroMemory(Destination, Length);
                return;
            }
            // Start from fresh audio instead of whatever piled up while idle.
            ReadPos = WritePos - NOISEMIC_TARGET_BYTES;
            Primed = TRUE;
        }
        else if (available > NOISEMIC_HIGH_BYTES)
        {
            // The app's clock runs fast: drop back to the target latency.
            ReadPos = WritePos - NOISEMIC_TARGET_BYTES;
            Overruns++;
        }

        available = WritePos - ReadPos;
        ULONG count = available < Length ? (ULONG)available : Length;
        ULONG offset = (ULONG)(ReadPos % NOISEMIC_RING_BYTES);
        ULONG first = count < NOISEMIC_RING_BYTES - offset ? count : NOISEMIC_RING_BYTES - offset;
        RtlCopyMemory(Destination, Data + offset, first);
        RtlCopyMemory(Destination + first, Data, count - first);
        ReadPos += count;

        if (count < Length)
        {
            // The app stopped or fell behind: pad with silence and re-prime.
            RtlZeroMemory(Destination + count, Length - count);
            Primed = FALSE;
            Underruns++;
        }
    }
};

#endif // _NOISEMIC_NOISERING_H_
