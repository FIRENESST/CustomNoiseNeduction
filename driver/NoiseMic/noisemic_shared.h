/*++

Module Name:

    noisemic_shared.h

Abstract:

    The contract between the driver and the app: property ids, the stream
    format and the status layout. No kernel headers, so the user-mode test
    can include it. virtual_mic.py mirrors these values.

--*/

#ifndef _NOISEMIC_SHARED_H_
#define _NOISEMIC_SHARED_H_

// Property set {28ACEC45-31B3-4F23-828D-1DC330D0665C}, declared in noisefeed.h.
typedef enum
{
    KSPROPERTY_NOISEMIC_WRITE  = 0,     // SET: PCM bytes to append.
    KSPROPERTY_NOISEMIC_STATUS = 1,     // GET: NOISEMIC_STATUS.
} KSPROPERTY_NOISEMIC;

#define NOISEMIC_INTERFACE_VERSION      1
#define NOISEMIC_SAMPLE_RATE            48000
#define NOISEMIC_CHANNELS               1
#define NOISEMIC_BITS_PER_SAMPLE        16
#define NOISEMIC_BLOCK_ALIGN            (NOISEMIC_CHANNELS * NOISEMIC_BITS_PER_SAMPLE / 8)
#define NOISEMIC_BYTES_PER_MS           (NOISEMIC_SAMPLE_RATE / 1000 * NOISEMIC_BLOCK_ALIGN)

// Largest write accepted at once (about 170 ms).
#define NOISEMIC_MAX_WRITE_BYTES        16384

typedef struct _NOISEMIC_STATUS
{
    ULONG Version;
    ULONG SampleRate;
    ULONG Channels;
    ULONG BitsPerSample;
    ULONG CaptureStreams;   // Capture streams currently running.
    ULONG BufferedBytes;    // Audio waiting to be captured.
    ULONG Underruns;        // Times the capture side ran dry.
    ULONG Overruns;         // Times old audio was dropped to cap latency.
} NOISEMIC_STATUS, *PNOISEMIC_STATUS;

#endif // _NOISEMIC_SHARED_H_
