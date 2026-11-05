/*++

Module Name:

    noisefeed.cpp

Abstract:

    Shared buffer for the audio the noise reduction app hands to the
    microphone endpoint, and the KS property that receives it.

--*/

#include "definitions.h"
#include "noisefeed.h"
#include "noisering.h"

// One root-enumerated device exposes one microphone, so a single buffer serves it.
// Globals live in non-paged memory, which the DPC-level reader requires.
static KSPIN_LOCK   g_FeedLock;
static NoiseRing    g_FeedRing;
static ULONG        g_CaptureStreams;

#pragma code_seg("INIT")
VOID NoiseFeedInitialize()
{
    KeInitializeSpinLock(&g_FeedLock);
    g_FeedRing.Reset();
    g_CaptureStreams = 0;
}

#pragma code_seg()
_Use_decl_annotations_
VOID NoiseFeedWrite(const BYTE* Data, ULONG Length)
{
    KIRQL irql;
    KeAcquireSpinLock(&g_FeedLock, &irql);
    g_FeedRing.Write(Data, Length);
    KeReleaseSpinLock(&g_FeedLock, irql);
}

#pragma code_seg()
_Use_decl_annotations_
VOID NoiseFeedRead(BYTE* Buffer, ULONG Length)
{
    KIRQL irql;
    KeAcquireSpinLock(&g_FeedLock, &irql);
    g_FeedRing.Read(Buffer, Length);
    KeReleaseSpinLock(&g_FeedLock, irql);
}

#pragma code_seg()
_Use_decl_annotations_
VOID NoiseFeedCaptureRunning(BOOLEAN Running)
{
    KIRQL irql;
    KeAcquireSpinLock(&g_FeedLock, &irql);
    if (Running)
    {
        g_CaptureStreams++;
        // Each new capture starts with fresh audio at the target latency.
        g_FeedRing.Primed = FALSE;
    }
    else if (g_CaptureStreams > 0)
    {
        g_CaptureStreams--;
    }
    KeReleaseSpinLock(&g_FeedLock, irql);
}

#pragma code_seg()
_Use_decl_annotations_
VOID NoiseFeedGetStatus(PNOISEMIC_STATUS Status)
{
    KIRQL irql;
    KeAcquireSpinLock(&g_FeedLock, &irql);
    Status->Version = NOISEMIC_INTERFACE_VERSION;
    Status->SampleRate = NOISEMIC_SAMPLE_RATE;
    Status->Channels = NOISEMIC_CHANNELS;
    Status->BitsPerSample = NOISEMIC_BITS_PER_SAMPLE;
    Status->CaptureStreams = g_CaptureStreams;
    Status->BufferedBytes = g_FeedRing.Buffered();
    Status->Underruns = g_FeedRing.Underruns;
    Status->Overruns = g_FeedRing.Overruns;
    KeReleaseSpinLock(&g_FeedLock, irql);
}

//=============================================================================
#pragma code_seg("PAGE")
NTSTATUS PropertyHandler_NoiseFeed
(
    _In_ PPCPROPERTY_REQUEST PropertyRequest
)
/*++

Routine Description:

  Handles ( KSPROPSETID_NoiseMicFeed, KSPROPERTY_NOISEMIC_WRITE / STATUS )
  on the capture wave filter. PortCls has already captured the caller's
  buffer into system memory.

--*/
{
    PAGED_CODE();

    if (PropertyRequest->Verb & KSPROPERTY_TYPE_BASICSUPPORT)
    {
        return PropertyHandler_BasicSupport(PropertyRequest, PropertyRequest->PropertyItem->Flags, VT_ILLEGAL);
    }

    switch (PropertyRequest->PropertyItem->Id)
    {
    case KSPROPERTY_NOISEMIC_WRITE:
        if (!(PropertyRequest->Verb & KSPROPERTY_TYPE_SET))
        {
            return STATUS_INVALID_DEVICE_REQUEST;
        }
        if (PropertyRequest->Value == NULL
            || PropertyRequest->ValueSize == 0
            || PropertyRequest->ValueSize > NOISEMIC_MAX_WRITE_BYTES
            || PropertyRequest->ValueSize % NOISEMIC_BLOCK_ALIGN != 0)
        {
            return STATUS_INVALID_PARAMETER;
        }
        NoiseFeedWrite(static_cast<const BYTE*>(PropertyRequest->Value), PropertyRequest->ValueSize);
        return STATUS_SUCCESS;

    case KSPROPERTY_NOISEMIC_STATUS:
        if (!(PropertyRequest->Verb & KSPROPERTY_TYPE_GET))
        {
            return STATUS_INVALID_DEVICE_REQUEST;
        }
        if (PropertyRequest->ValueSize == 0)
        {
            PropertyRequest->ValueSize = sizeof(NOISEMIC_STATUS);
            return STATUS_BUFFER_OVERFLOW;
        }
        if (PropertyRequest->Value == NULL || PropertyRequest->ValueSize < sizeof(NOISEMIC_STATUS))
        {
            PropertyRequest->ValueSize = 0;
            return STATUS_BUFFER_TOO_SMALL;
        }
        NoiseFeedGetStatus(static_cast<PNOISEMIC_STATUS>(PropertyRequest->Value));
        PropertyRequest->ValueSize = sizeof(NOISEMIC_STATUS);
        return STATUS_SUCCESS;

    default:
        return STATUS_INVALID_DEVICE_REQUEST;
    }
}

#pragma code_seg()
