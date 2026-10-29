using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Refrain.Core;
using Refrain.Core.Tags;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage;
using Windows.System;

namespace Refrain.Views;

/// <summary>The Metadata tab (SPEC §7, §36 "5."): one file's or several files' fields, the front cover, and one file's
/// details. Reads and saves run off the UI thread; only changed fields are written.</summary>
public sealed partial class MetadataPage : UserControl
{
    const int CoverSize = 220;
    const long LargeCover = 1024 * 1024;

    readonly Func<XamlRoot> xamlRoot;
    readonly Func<nint> window;
    readonly SemaphoreSlim saving = new(1, 1);
    readonly Image cover = new() { Width = CoverSize, Height = CoverSize, Stretch = Microsoft.UI.Xaml.Media.Stretch.Uniform };
    readonly Border coverFrame;
    readonly TextBlock coverSource = Ui.Secondary();
    readonly Button replace = Ui.Button("Replace…", "A JPEG or PNG for the front cover; or drop one onto the cover");
    readonly Button remove = Ui.Button("Remove", "Remove the embedded front cover");
    readonly TextBlock name = Ui.Title();
    readonly TextBlock formatLine = Ui.Secondary();
    readonly TextBlock note = Ui.Secondary();
    readonly Button revert = Ui.Button("Revert", "Drop the edits (Esc)");
    readonly Button save = Ui.Primary("Save");
    readonly Grid details = Ui.DetailsGrid();
    readonly Dictionary<Field, (TextBox Box, Button Clear)> boxes = [];

    List<FileTags> files = [];
    Dictionary<Field, string?> common = [];
    readonly HashSet<Field> touched = [];
    readonly HashSet<Field> cleared = [];
    TagFile.CoverChange? coverChange;
    bool filling;
    int generation;

    /// <summary>Files were saved: their folders need re-reading into the index.</summary>
    public event Action<IReadOnlyList<string>>? Saved;
    /// <summary>The edits became dirty or clean.</summary>
    public event Action<bool>? DirtyChanged;

    public MetadataPage(Func<XamlRoot> xamlRoot, Func<nint> window)
    {
        this.xamlRoot = xamlRoot;
        this.window = window;
        ToolTipService.SetToolTip(save, "Write the edited fields to the files (Ctrl+S)");
        var stack = new StackPanel { Spacing = 12, Padding = new Thickness(16) };

        coverFrame = new Border
        {
            Child = cover,
            Width = CoverSize,
            Height = CoverSize,
            CornerRadius = new CornerRadius(4),
            Background = Ui.Brush("SubtleFillColorSecondaryBrush"),
            HorizontalAlignment = HorizontalAlignment.Left,
            AllowDrop = true,
        };
        coverFrame.DragOver += (_, e) =>
        {
            if (!e.DataView.Contains(StandardDataFormats.StorageItems) || !Editable) return;
            e.AcceptedOperation = DataPackageOperation.Copy;
            e.DragUIOverride.Caption = "Use as the front cover";
            e.Handled = true;
        };
        coverFrame.Drop += async (_, e) =>
        {
            e.Handled = true;
            var items = await e.DataView.GetStorageItemsAsync();
            if (items.OfType<StorageFile>().FirstOrDefault() is { } file) await UseCover(file.Path);
        };
        stack.Children.Add(coverFrame);
        stack.Children.Add(coverSource);
        var coverButtons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        coverButtons.Children.Add(replace);
        coverButtons.Children.Add(remove);
        stack.Children.Add(coverButtons);
        stack.Children.Add(name);
        stack.Children.Add(formatLine);
        stack.Children.Add(note);
        stack.Children.Add(Fields());
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right };
        actions.Children.Add(revert);
        actions.Children.Add(save);
        stack.Children.Add(actions);
        stack.Children.Add(details);
        Content = new ScrollViewer { Content = stack, HorizontalScrollMode = ScrollMode.Disabled };

        replace.Click += async (_, _) => await PickCover();
        remove.Click += (_, _) =>
        {
            coverChange = new TagFile.CoverChange(null);
            _ = ShowCover();
            Changed();
        };
        save.Click += async (_, _) => await Save();
        revert.Click += (_, _) => Revert();
        KeyDown += (_, e) =>
        {
            if (e.Key != VirtualKey.Escape || !Dirty) return;
            e.Handled = true;
            Revert();
        };
        var saveKey = new KeyboardAccelerator { Key = VirtualKey.S, Modifiers = VirtualKeyModifiers.Control };
        saveKey.Invoked += async (_, e) =>
        {
            e.Handled = true;
            await Save();
        };
        KeyboardAccelerators.Add(saveKey);
    }

    /// <summary>A row per field: its label, its box, and the clear button several files show.</summary>
    Grid Fields()
    {
        var grid = new Grid { ColumnSpacing = 8, RowSpacing = 8 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        foreach (var field in Enum.GetValues<Field>())
        {
            var row = grid.RowDefinitions.Count;
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            var label = new TextBlock { Text = TagFile.Label(field), VerticalAlignment = VerticalAlignment.Center };
            Grid.SetRow(label, row);
            grid.Children.Add(label);
            var box = new TextBox
            {
                AcceptsReturn = field == Field.Comment,
                TextWrapping = field == Field.Comment ? TextWrapping.Wrap : TextWrapping.NoWrap,
                InputScope = TagFile.Numbers.Contains(field) ? Scope(InputScopeNameValue.Number) : null,
            };
            Grid.SetRow(box, row);
            Grid.SetColumn(box, 1);
            grid.Children.Add(box);
            var clear = new Button
            {
                Content = new FontIcon { Glyph = "", FontSize = 12 },
                Padding = new Thickness(6),
                Visibility = Visibility.Collapsed,
                VerticalAlignment = VerticalAlignment.Center,
            };
            ToolTipService.SetToolTip(clear, $"Clear {TagFile.Label(field)} on every selected file");
            Grid.SetRow(clear, row);
            Grid.SetColumn(clear, 2);
            grid.Children.Add(clear);
            box.TextChanged += (_, _) =>
            {
                if (filling) return;
                touched.Add(field);
                cleared.Remove(field);
                if (files.Count > 1) box.PlaceholderText = common.GetValueOrDefault(field) is null ? "(multiple values)" : "";
                Changed();
            };
            clear.Click += (_, _) =>
            {
                filling = true;
                box.Text = "";
                filling = false;
                cleared.Add(field);
                touched.Add(field);
                box.PlaceholderText = "(cleared on all files)";
                Changed();
            };
            boxes[field] = (box, clear);
        }
        return grid;
    }

    static InputScope Scope(InputScopeNameValue value)
    {
        var scope = new InputScope();
        scope.Names.Add(new InputScopeName(value));
        return scope;
    }

    public bool Dirty => Editable && (coverChange is not null || Changes().Values.Any(c => c.Count > 0));

    bool Editable => files.Count > 0 && files.All(f => f.ReadOnly is null);

    void Changed()
    {
        var dirty = Dirty;
        save.IsEnabled = revert.IsEnabled = dirty;
        DirtyChanged?.Invoke(dirty);
    }

    /// <summary>Each file's fields to write.</summary>
    Dictionary<FileTags, Dictionary<Field, string>> Changes()
    {
        var texts = boxes.ToDictionary(b => b.Key, b => b.Value.Box.Text);
        if (files.Count == 1)
        {
            var only = files[0];
            var single = TagEdits.SingleChanges(only, texts);
            if (only.NoTrackTotal) single.Remove(Field.TrackTotal);
            return new() { [only] = single };
        }
        var shared = TagEdits.MultiChanges(texts, touched, cleared, common);
        return files.ToDictionary(f => f, f =>
        {
            var mine = new Dictionary<Field, string>(shared);
            if (f.NoTrackTotal) mine.Remove(Field.TrackTotal);
            return mine;
        });
    }

    /// <summary>Reads the files' tags off the UI thread, then fills the page; <paramref name="skipped"/> notes items
    /// that can't be edited here.</summary>
    public async Task Load(IReadOnlyList<string> paths, IReadOnlyList<string> skipped)
    {
        var mine = ++generation;
        var read = await Task.Run(() => paths.Select(path =>
        {
            try
            {
                return (Tags: TagFile.Read(path), Error: (string?)null, Path: path);
            }
            catch (Exception e)
            {
                return (Tags: (FileTags?)null, Error: e.Message, Path: path);
            }
        }).ToList());
        if (mine != generation) return;  // another selection took over
        files = read.Where(r => r.Tags is not null).Select(r => r.Tags!).ToList();
        var notes = new List<string>();
        notes.AddRange(read.Where(r => r.Error is not null).Select(r => $"{Path.GetFileName(r.Path)} can't be read: {r.Error}"));
        if (skipped.Count > 0)
            notes.Add($"Left out ({skipped.Count}): {string.Join(", ", skipped.Take(5))}{(skipped.Count > 5 ? "…" : "")}, " +
                      "not audio files with tags.");
        if (files.FirstOrDefault(f => f.ReadOnly is not null) is { } readOnly) notes.Add(readOnly.ReadOnly!);
        Fill(notes);
    }

    void Fill(List<string> notes)
    {
        filling = true;
        touched.Clear();
        cleared.Clear();
        coverChange = null;
        var several = files.Count > 1;
        common = several ? TagEdits.Common(files) : [];
        foreach (var (field, (box, clear)) in boxes)
        {
            var perTrack = TagEdits.PerTrack.Contains(field);
            box.Text = files.Count == 0 ? ""
                : several ? perTrack ? "" : common[field] ?? ""
                : files[0].Values[field];
            box.PlaceholderText = several && !perTrack && common[field] is null ? "(multiple values)" : "";
            box.IsEnabled = Editable && !(several && perTrack) && !(field == Field.TrackTotal && files.Any(f => f.NoTrackTotal));
            ToolTipService.SetToolTip(box, field == Field.TrackTotal && files.Any(f => f.NoTrackTotal)
                ? "WMA has no standard track total."
                : several && perTrack ? "Edited one file at a time." : null);
            clear.Visibility = several && !perTrack && Editable ? Visibility.Visible : Visibility.Collapsed;
        }
        filling = false;
        name.Text = several ? $"{files.Count} files" : files.Count == 1 ? Path.GetFileName(files[0].Path) : "";
        formatLine.Text = several ? "" : files.Count == 1 ? files[0].Details.FormatLine : "";
        note.Text = string.Join("\n", notes);
        note.Visibility = notes.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        replace.IsEnabled = Editable;
        FillDetails();
        _ = ShowCover();
        Changed();
    }

    void FillDetails()
    {
        details.Children.Clear();
        details.RowDefinitions.Clear();
        if (files.Count != 1) return;
        var (f, d) = (files[0], files[0].Details);
        Ui.Detail(details, "Path", f.Path);
        Ui.Detail(details, "Size", Core.Browser.Columns.Size(d.Size));
        Ui.Detail(details, "Codec", d.Codec);
        Ui.Detail(details, "Duration", Core.Browser.Columns.Duration(d.Duration));
        Ui.Detail(details, "Bitrate", d.Bitrate is { } b ? $"{b} kbps" : null);
        Ui.Detail(details, "Sample rate", d.SampleRate is { } r ? $"{r / 1000.0:0.###} kHz" : null);
        Ui.Detail(details, "Bit depth", d.BitDepth is { } bits ? $"{bits}-bit" : null);
        Ui.Detail(details, "Channels", d.Channels?.ToString());
        Ui.Detail(details, "Track gain", d.TrackGain);
        Ui.Detail(details, "Track peak", d.TrackPeak);
        Ui.Detail(details, "Album gain", d.AlbumGain);
        Ui.Detail(details, "Album peak", d.AlbumPeak);
    }

    /// <summary>The pending cover, else the files' common embedded cover, else (one file) its folder's image.</summary>
    async Task ShowCover()
    {
        var mine = generation;
        byte[]? data = null;
        string source;
        if (coverChange is { Image: var pending })
        {
            data = pending?.Data;
            source = pending is null ? "Cover removed (not saved yet)" : "New cover (not saved yet)";
        }
        else if (files.Count == 0)
        {
            source = "";
        }
        else if (files.All(f => f.Cover is null))
        {
            source = "No cover";
            if (files.Count == 1 && FolderImage(files[0].Path) is { } image)
            {
                data = await Task.Run(() => File.ReadAllBytes(image));
                source = $"{Path.GetFileName(image)} in the folder (not embedded)";
            }
        }
        else if (files.Select(f => f.Cover?.Data).Distinct(ByteArrays.Instance).Count() == 1)
        {
            data = files[0].Cover!.Data;
            source = "Embedded cover";
        }
        else
        {
            source = "(multiple covers)";
        }
        remove.IsEnabled = Editable && (coverChange is { Image: not null } || coverChange is null && files.Any(f => f.Cover is not null));
        coverSource.Text = source;
        var bitmap = data is null ? null : await Ui.Image(data, CoverSize * 2);
        if (mine == generation) cover.Source = bitmap;
    }

    static string? FolderImage(string file)
    {
        var folder = Path.GetDirectoryName(file);
        if (folder is null) return null;
        foreach (var name in new[] { "folder.jpg", "cover.jpg", "front.jpg", "folder.png", "cover.png", "front.png" })
        {
            var path = Path.Combine(folder, name);
            if (File.Exists(path)) return path;
        }
        return null;
    }

    async Task PickCover()
    {
        var picker = new Windows.Storage.Pickers.FileOpenPicker();
        picker.FileTypeFilter.Add(".jpg");
        picker.FileTypeFilter.Add(".jpeg");
        picker.FileTypeFilter.Add(".png");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, window());
        if (await picker.PickSingleFileAsync() is { } file) await UseCover(file.Path);
    }

    /// <summary>A JPEG or PNG, checked by its signature; over 1 MB, a confirmation (every file carries its own copy).</summary>
    async Task UseCover(string path)
    {
        var data = await Task.Run(() => File.ReadAllBytes(path));
        var mime = TagFile.ImageType(data);
        if (mime is null)
        {
            note.Text = $"{Path.GetFileName(path)} isn't a JPEG or PNG image.";
            note.Visibility = Visibility.Visible;
            return;
        }
        if (data.Length > LargeCover)
        {
            var dialog = new ContentDialog
            {
                Title = "Use a large cover?",
                Content = $"{Path.GetFileName(path)} is {Core.Browser.Columns.Size(data.Length)}. Every file carries its " +
                          $"own copy, so each of the {files.Count} files grows by that much.",
                PrimaryButtonText = "Use it",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
                XamlRoot = xamlRoot(),
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
        }
        coverChange = new TagFile.CoverChange(new CoverImage(data, mime));
        await ShowCover();
        Changed();
    }

    public void Revert()
    {
        var notes = note.Text.Length > 0 ? note.Text.Split('\n').ToList() : [];
        Fill(notes);
    }

    /// <summary>Writes the edits, one file at a time on a worker; on failure the edits stay for a retry.</summary>
    public async Task<bool> Save()
    {
        if (!Dirty) return true;
        var changes = Changes();
        var cover = coverChange;
        save.IsEnabled = revert.IsEnabled = false;
        await saving.WaitAsync();
        List<string> failed;
        try
        {
            failed = await Task.Run(() =>
            {
                var errors = new List<string>();
                foreach (var (file, fields) in changes)
                {
                    if (fields.Count == 0 && cover is null) continue;
                    try
                    {
                        TagFile.Save(file.Path, fields, cover);
                    }
                    catch (TagSaveException e)
                    {
                        errors.Add(e.Message);
                    }
                    catch (Exception e)
                    {
                        Log.Error($"Saving {file.Path} failed", e);
                        errors.Add($"{Path.GetFileName(file.Path)}: {e.Message}");
                    }
                }
                return errors;
            });
        }
        finally
        {
            saving.Release();
        }
        Saved?.Invoke(changes.Keys.Select(f => f.Path).ToList());
        if (failed.Count > 0)
        {
            note.Text = (files.Count > 1 ? $"Saved {files.Count - failed.Count} of {files.Count} files. Not saved: " : "Not saved: ") +
                        string.Join(" ", failed);
            note.Visibility = Visibility.Visible;
            Changed();
            return false;
        }
        await Load(files.Select(f => f.Path).ToList(), []);
        return true;
    }

    sealed class ByteArrays : IEqualityComparer<byte[]?>
    {
        public static readonly ByteArrays Instance = new();
        public bool Equals(byte[]? a, byte[]? b) => a is null ? b is null : b is not null && a.AsSpan().SequenceEqual(b);
        public int GetHashCode(byte[]? data) => data is null ? 0 : HashCode.Combine(data.Length, data.Length > 0 ? data[0] : 0);
    }
}
