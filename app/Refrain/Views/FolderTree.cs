using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Refrain.Core.Index;

namespace Refrain.Views;

/// <summary>The folder tree (SPEC §5, §33): roots at the top level, folders only, in Explorer's order, expanded
/// lazily; the expanded folders are remembered by path. Add folder… and the empty library's hint live here (§3).</summary>
public sealed partial class FolderTree : Grid
{
    /// <summary>A tree node's folder: its entry, name and path; a root's top entry also carries its root.</summary>
    sealed class Folder(long id, string name, string path, Root? root)
    {
        public long Id { get; } = id;
        public string Path { get; } = path;
        public Root? Root { get; } = root;
        public bool Loaded { get; set; }
        public override string ToString() => Root is { Online: false } ? $"{name} (offline)" : name;
    }

    readonly TreeView tree = new() { SelectionMode = TreeViewSelectionMode.Single, CanDragItems = false, CanReorderItems = false };
    readonly TextBlock hint = new()
    {
        Text = "Add your music folder to start. Its folders and files can then be browsed and searched.",
        TextWrapping = TextWrapping.Wrap,
        Margin = new Thickness(12, 4, 12, 8),
    };

    public event Action<long>? FolderChosen;
    public event Action? AddRequested;
    public event Action<Root>? RemoveRequested;
    public event Action<long>? RescanRequested;
    /// <summary>A folder's task entries (Explorer's, SPEC §35), as menu items.</summary>
    public Func<string, List<MenuFlyoutItemBase>>? EntryItems { get; set; }
    public Func<long, Task<List<FolderNode>>>? Subfolders { get; set; }

    public FolderTree()
    {
        RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        var add = new Button
        {
            Content = "Add folder…",
            Margin = new Thickness(12, 12, 12, 4),
            HorizontalAlignment = HorizontalAlignment.Stretch,
        };
        ToolTipService.SetToolTip(add, "Add a music folder to the library (Ctrl+O)");
        add.Click += (_, _) => AddRequested?.Invoke();
        Children.Add(add);
        SetRow(hint, 1);
        Children.Add(hint);
        SetRow(tree, 2);
        Children.Add(tree);
        tree.Expanding += async (_, e) => await LoadChildren(e.Node);
        tree.ItemInvoked += (_, e) =>
        {
            if (e.InvokedItem is TreeViewNode { Content: Folder folder }) FolderChosen?.Invoke(folder.Id);
        };
        tree.ContextRequested += OnContextRequested;
    }

    public bool IsEmpty => tree.RootNodes.Count == 0;

    /// <summary>The roots, with their top entries; the remembered folders expand again.</summary>
    public async Task Load(List<(Root Root, long Top)> roots, IReadOnlyCollection<string> expanded)
    {
        var keep = ExpandedPaths().Concat(expanded).ToHashSet(StringComparer.OrdinalIgnoreCase);
        tree.RootNodes.Clear();
        foreach (var (root, top) in roots)
        {
            var node = new TreeViewNode { Content = new Folder(top, root.Path, root.Path, root), HasUnrealizedChildren = true };
            tree.RootNodes.Add(node);
            await Restore(node, keep);
        }
        hint.Visibility = roots.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    async Task Restore(TreeViewNode node, HashSet<string> expanded)
    {
        if (node.Content is not Folder folder || !expanded.Contains(folder.Path)) return;
        await LoadChildren(node);
        node.IsExpanded = true;
        foreach (var child in node.Children) await Restore(child, expanded);
    }

    async Task LoadChildren(TreeViewNode node)
    {
        if (node.Content is not Folder folder || folder.Loaded || Subfolders is null) return;
        folder.Loaded = true;
        var children = await Subfolders(folder.Id);
        node.Children.Clear();
        foreach (var child in children)
            node.Children.Add(new TreeViewNode
            {
                Content = new Folder(child.Id, child.Name, System.IO.Path.Combine(folder.Path, child.Name), null),
                HasUnrealizedChildren = child.HasSubfolders,
            });
        node.HasUnrealizedChildren = false;
    }

    /// <summary>A folder's subfolders changed: a loaded node reloads them, keeping what was expanded.</summary>
    public async Task Refresh(long folderId)
    {
        var node = Find(tree.RootNodes, folderId);
        if (node?.Content is not Folder { Loaded: true } folder) return;
        var expanded = ExpandedPaths().ToHashSet(StringComparer.OrdinalIgnoreCase);
        folder.Loaded = false;
        await LoadChildren(node);
        foreach (var child in node.Children) await Restore(child, expanded);
    }

    /// <summary>Expands the tree down to this folder (its chain of ids from the top entry) and selects it.</summary>
    public async Task Reveal(IReadOnlyList<long> chain)
    {
        if (Visibility != Visibility.Visible || chain.Count == 0) return;
        var node = tree.RootNodes.FirstOrDefault(n => n.Content is Folder f && f.Id == chain[0]);
        for (var i = 1; node is not null && i < chain.Count; i++)
        {
            await LoadChildren(node);
            node.IsExpanded = true;
            node = node.Children.FirstOrDefault(n => n.Content is Folder f && f.Id == chain[i]);
        }
        if (node is not null) tree.SelectedNode = node;
    }

    public List<string> ExpandedPaths()
    {
        var paths = new List<string>();
        void Walk(IEnumerable<TreeViewNode> nodes)
        {
            foreach (var node in nodes)
            {
                if (!node.IsExpanded || node.Content is not Folder folder) continue;
                paths.Add(folder.Path);
                Walk(node.Children);
            }
        }
        Walk(tree.RootNodes);
        return paths;
    }

    static TreeViewNode? Find(IEnumerable<TreeViewNode> nodes, long id)
    {
        foreach (var node in nodes)
        {
            if (node.Content is Folder f && f.Id == id) return node;
            if (Find(node.Children, id) is { } found) return found;
        }
        return null;
    }

    void OnContextRequested(UIElement sender, ContextRequestedEventArgs e)
    {
        DependencyObject? source = e.OriginalSource as DependencyObject;
        while (source is not null and not TreeViewItem) source = VisualTreeHelper.GetParent(source);
        if (source is not TreeViewItem item || tree.NodeFromContainer(item)?.Content is not Folder folder) return;
        var menu = new MenuFlyout();
        var open = new MenuFlyoutItem { Text = "Open" };
        open.Click += (_, _) => FolderChosen?.Invoke(folder.Id);
        menu.Items.Add(open);
        var explorer = new MenuFlyoutItem { Text = "Open in File Explorer" };
        explorer.Click += (_, _) => Shell.Reveal(folder.Path, select: false);
        menu.Items.Add(explorer);
        var rescan = new MenuFlyoutItem { Text = "Rescan" };
        rescan.Click += (_, _) => RescanRequested?.Invoke(folder.Id);
        menu.Items.Add(rescan);
        if (EntryItems?.Invoke(folder.Path) is { Count: > 0 } entries)
        {
            menu.Items.Add(new MenuFlyoutSeparator());
            foreach (var entry in entries) menu.Items.Add(entry);
        }
        if (folder.Root is { } root)
        {
            menu.Items.Add(new MenuFlyoutSeparator());
            var remove = new MenuFlyoutItem { Text = "Remove from library…" };
            remove.Click += (_, _) => RemoveRequested?.Invoke(root);
            menu.Items.Add(remove);
        }
        if (e.TryGetPosition(item, out var point)) menu.ShowAt(item, point);
        else menu.ShowAt(item);
        e.Handled = true;
    }
}
