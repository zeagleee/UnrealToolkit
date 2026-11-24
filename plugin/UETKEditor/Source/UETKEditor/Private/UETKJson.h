// UnrealToolkit: JSON results for the scripting libraries ({"ok": true, ...} / {"ok": false, "error": ...}).
#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace UETKJson
{
	using FJson = TSharedRef<FJsonObject>;

	inline FString Serialize(const FJson& Object)
	{
		FString Out;
		const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Out);
		FJsonSerializer::Serialize(Object, Writer);
		return Out;
	}

	inline FString Ok(FJson Object = MakeShared<FJsonObject>())
	{
		Object->SetBoolField(TEXT("ok"), true);
		return Serialize(Object);
	}

	inline FString Fail(const FString& Error)
	{
		FJson Object = MakeShared<FJsonObject>();
		Object->SetBoolField(TEXT("ok"), false);
		Object->SetStringField(TEXT("error"), Error);
		return Serialize(Object);
	}

	/** Parse a JSON object ("" = empty object). */
	inline TSharedPtr<FJsonObject> Parse(const FString& Text)
	{
		if (Text.TrimStartAndEnd().IsEmpty())
		{
			return MakeShared<FJsonObject>();
		}
		TSharedPtr<FJsonObject> Object;
		const TSharedRef<TJsonReader<TCHAR>> Reader = TJsonReaderFactory<TCHAR>::Create(Text);
		return FJsonSerializer::Deserialize(Reader, Object) ? Object : nullptr;
	}

	/** Lower case without spaces, underscores or dashes. */
	inline FString Norm(const FString& In)
	{
		FString Out;
		Out.Reserve(In.Len());
		for (TCHAR C : In)
		{
			if (C != TEXT(' ') && C != TEXT('_') && C != TEXT('-'))
			{
				Out.AppendChar(FChar::ToLower(C));
			}
		}
		return Out;
	}
}
