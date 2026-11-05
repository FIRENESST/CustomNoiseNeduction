/*++

Module Name:

    noisefeed.h

Abstract:

    Audio handed over by the noise reduction app. The app writes 48 kHz,
    16-bit mono PCM to the wave filter with a KS property; the capture stream
    reads it back into the WaveRT buffer. A small jitter buffer absorbs the
    timing difference between the app's microphone clock and this device.

--*/

#ifndef _NOISEMIC_NOISEFEED_H_
#define _NOISEMIC_NOISEFEED_H_

// The app and the driver share these values. Keep virtual_mic.py in sync.
// {28ACEC45-31B3-4F23-828D-1DC330D0665C}
#define STATIC_KSPROPSETID_NoiseMicFeed\
    0x28acec45, 0x31b3, 0x4f23, 0x82, 0x8d, 0x1d, 0xc3, 0x30, 0xd0, 0x66, 0x5c
DEFINE_GUIDSTRUCT("28ACEC45-31B3-4F23-828D-1DC330D0665C", KSPROPSETID_NoiseMicFeed);
#define KSPROPSETID_NoiseMicFeed DEFINE_GUIDNAMED(KSPROPSETID_NoiseMicFeed)

#include "noisemic_shared.h"

VOID NoiseFeedInitialize();

_IRQL_requires_max_(DISPATCH_LEVEL)
VOID NoiseFeedWrite
(
    _In_reads_bytes_(Length) const BYTE* Data,
    _In_ ULONG Length
);

_IRQL_requires_max_(DISPATCH_LEVEL)
VOID NoiseFeedRead
(
    _Out_writes_bytes_(Length) BYTE* Buffer,
    _In_ ULONG Length
);

_IRQL_requires_max_(DISPATCH_LEVEL)
VOID NoiseFeedCaptureRunning
(
    _In_ BOOLEAN Running
);

_IRQL_requires_max_(DISPATCH_LEVEL)
VOID NoiseFeedGetStatus
(
    _Out_ PNOISEMIC_STATUS Status
);

NTSTATUS PropertyHandler_NoiseFeed
(
    _In_ PPCPROPERTY_REQUEST PropertyRequest
);

#endif // _NOISEMIC_NOISEFEED_H_
