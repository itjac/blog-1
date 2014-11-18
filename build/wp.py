import os
import re
import shutil
import markdown
from wordpress_xmlrpc import (
        Client, WordPressPost, WordPressPage, 
        WordPressTaxonomy, WordPressTerm)
import wordpress_xmlrpc
from wordpress_xmlrpc.methods.posts import (
        GetPosts, NewPost, GetPost, EditPost)
from wordpress_xmlrpc.methods.users import GetUserInfo
from wordpress_xmlrpc.methods.options import GetOptions
from wordpress_xmlrpc.methods.taxonomies import (
        GetTaxonomies, GetTaxonomy, 
        GetTerms, GetTerm, NewTerm, EditTerm, DeleteTerm)
from zrong.base import slog, read_file, write_by_templ, DictBase


conf = None
args = None
wp = None

def _wpcall(method):
    global wp
    if not wp:
        wp = Client(conf.site.url, conf.site.user, conf.site.password)
    try:
        results = wp.call(method)
    except wordpress_xmlrpc.exceptions.InvalidCredentialsError as e:
        slog.error(e)
        return None
    return results

def _get_postid(as_list=False):
    if not args.query:
        return None
    if as_list:
        postids = []
        for postid in args.query:
            match = re.match(r'^(\d+)-(\d+)$', postid)
            if match:
                a = int(match.group(1))
                b = int(match.group(2))
                for i in range(a,b+1):
                    postids.append(str(i))
            else:
                postids.append(postid)
        return postids
    return args.query[0]

def _get_terms_from_wp(query, force=False):
    if len(query )== 0:
        slog.error('Please provide a taxonomy name! You can use '
                '"-c show -t tax" to get one.')
        return None
    taxname = query[0]
    slug = args.query[1] if len(args.query)>1 else None
    terms = conf[taxname]
    if not terms or force:
        results = _wpcall(GetTerms(taxname))
        if results:
            conf.save_terms(results, taxname)
    if terms and slug:
        return terms[slug]
    return terms

def _print_result(result):
    if isinstance(result, WordPressTerm):
        slog.info('id=%s, group=%s, '
                'taxnomy_id=%s, name=%s, slug=%s, '
                'parent=%s, count=%d', 
                result.id, result.group, 
                result.taxonomy_id, result.name, result.slug,
                result.parent, result.count)
    elif isinstance(result, WordPressPost):
        slog.info('id=%s, date=%s, date_modified=%s, '
                'slug=%s, title=%s, post_status=%s, post_type=%s', 
                result.id, str(result.date), str(result.date_modified), 
                result.slug, result.title,
                result.post_status, result.post_type)
    else:
        slog.info(result)

def _print_results(results):
    if isinstance(results, list):
        for result in results:
            _print_result(result)
    elif isinstance(results, dict):
        for k,v in results.items():
            slog.info('%s %s'%(k, str(v)))
    else:
        _print_result(results)

def _get_article_content(afile):
    if not os.path.exists(afile):
        slog.error('The file "%s" is inexistance!'%afile)
        return None, None
    txt = read_file(afile)
    md = markdown.Markdown(extensions=[
        'markdown.extensions.meta',
        'markdown.extensions.tables',
        ])
    html = md.convert(txt)
    meta = md.Meta

    adict = DictBase()
    adict.title = meta['title'][0]
    adict.postid = meta['postid'][0]
    adict.nicename = meta['nicename'][0]
    adict.slug = meta['slug'][0]
    adict.date = meta['date'][0]
    adict.author = meta['author'][0]
    tags = meta.get('tags')
    if tags:
        adict.tags = [tag.strip() for tag in tags[0].split(',')]
    category = meta.get('category')
    if category:
        adict.category = [cat.strip() for cat in category[0].split(',')]
    modified = meta.get('modified')
    if modified:
        adict.modified = modified[0]
    posttype = meta.get('posttype')
    if posttype:
        adict.posttype = posttype[0]
    else:
        adict.posttype = 'post'
    poststatus = meta.get('poststatus')
    if poststatus:
        adict.poststatus = poststatus[0]
    else:
        adict.poststatus = 'publish'
    return html,adict 

def _get_terms_from_meta(categories, tags):
    terms = []
    if categories:
        for cat in categories:
            term = conf.get_term('category', cat)
            if not term:
                slog.error('The category "%s" is not in wordpress.'
                        ' Please create it first.'%cat)
                return None
            terms.append(term)
    if tags:
        for tag in tags:
            term = conf.get_term('post_tag', tag)
            if not term:
                slog.error('The tag "%s" is not in wordpress.'
                        'Please create it first'%tag)
                return None
            terms.append(term)
    return terms

def _wp_new():
    if args.type == 'draft':
        _wp_new_article()
    elif args.type == 'term':
        _wp_new_term()

def _wp_new_article():
    postid = _get_postid()
    if not postid:
        slog.warning('Please provide a post id!')
        return
    afile, aname = conf.get_draft(postid)
    html, meta = _get_article_content(afile)

    # Update all taxonomy befor new a article.
    _get_terms_from_wp(['category'])
    _get_terms_from_wp(['post_tag'])

    if meta.post_type == 'page':
        post = WordPressPage()
    else:
        post = WordPressPost()

    post.content= html
    post.title = meta.title
    post.slug = meta.nicename
    post.date = meta.date
    post.user = meta.author
    post.date_modified = meta.modified
    post.post_status = meta.poststatus
    post.terms = _get_terms_from_meta(meta.category, meta.tags)
    if not post.terms:
        slog.warning('Please provide some terms.')
        return
    postid = _wpcall(NewPost(post))

    if postid:
        write_by_templ(afile, afile, {'POSTID':postid, 'SLUG':postid})
    else:
        return

    newfile, newname = None, None
    if meta.post_type == 'page':
        newfile, newname = conf.get_article(post.nicename, meta.post_type)
    else:
        newfile, newname = conf.get_article(postid, meta.post_type)

    shutil.move(afile, newfile)
    slog.info('Move "%s" to "%s".'%(afile, newfile))

def _wp_new_term():
    if not args.query or len(args.query)<2:
        slog.error('Provide 2 arguments at least please.')
        return
    term = _get_terms_from_wp(args.query, force=True)
    if term:
        slog.error('The term "%s" has been in wordpress.'%args.query[1])
        return
    taxname = args.query[0]
    slug = args.query[1]
    name = args.query[2] if len(args.query)>2 else slug
    term = WordPressTerm()
    term.slug = slug
    term.name = name
    term.taxonomy = taxname
    if len(args.query)>3:
        term.description = args.query[3]
    termid = _wpcall(NewTerm(term))
    if not termid:
        return
    term = _wpcall(GetTerm(taxname, termid))
    if not term:
        return
    slog.info('The term %s(%s) has created.'%(name, termid))
    conf.save_term(term, taxname)
    conf.save_to_file()
    slog.info('The term %s has saved.'%name)

def _wp_update():
    if conf.is_article(args.type):
        _wp_update_article()
    elif args.type == 'term':
        _wp_update_term()

def _wp_update_article():
    postids = _get_postid(as_list=True)
    if not postids:
        slog.warning('Please provide a post id!')
        return

    # Update all taxonomy
    _get_terms_from_wp(['category'])
    _get_terms_from_wp(['post_tag'])

    for postid in postids:
        _update_a_article(postid)

def _update_a_article(postid):
    afile, aname = conf.get_article(postid, args.type)
    html, meta = _get_article_content(afile)
    if not html:
        return
    resultclass = WordPressPost
    if args.type == 'page':
        postid = meta.postid
        resultclass = WordPressPage
    elif args.type == 'draft':
        postid = meta.postid
        if meta.post_type == 'page':
            resultclass = WordPressPage

    post = _wpcall(GetPost(postid, result_class=resultclass))
    if not post:
        slog.warning('No post "%s"!'%postid)
        return
    slog.info('Old article:')
    _print_results(post)
    post.title = meta.title
    post.user = meta.author
    post.slug = meta.nicename
    post.date = meta.date
    post.content = html
    if meta.modified:
        post.date_modified = meta.modified
    post.post_status = meta.post_status

    terms = _get_terms_from_meta(meta.category, meta.tags)
    if terms:
        post.terms = terms
    else:
        slog.warning('Please provide some terms.')
        return

    succ = _wpcall(EditPost(postid, post))
    if succ == None:
        return
    if succ:
        slog.info('Update %s successfully!'%postid)
    else:
        slog.info('Update %s fail!'%postid)

def _wp_update_term():
    term = _get_terms_from_wp(args.query, force=True)
    if len(args.query) > 2:
        if not term:
            slog.error('The term %s is not existend.'%str(args.query))
            return
        taxname = args.query[0]
        term = _wpcall(GetTerm(taxname, term.id))
        if term:
            term.slug = args.query[1]
            term.name = args.query[2]
            if len(args.query)>3:
                term.description = args.query[3]
            # post_get can not support parent.
            if term.taxonomy == 'post_tag':
                term.parent = None
            issucc = _wpcall(EditTerm(term.id, term))
            if issucc:
                conf.save_term(term, taxname)
                conf.save_to_file()
                slog.info('The term %s(%s) has saved.'%(term.slug, term.id))
            else:
                slog.info('The term %s(%s) saves unsuccessfully.'%(term.slug,
                    term.id))
        else:
            slog.info('Can not get term "%s".'%args.query[1])
    else:
        if term:
            slog.info('Update terms done.')
        else:
            slog.warning('No terms.')

def _wp_del():
    pass

def _wp_show():
    method = None
    if args.type == 'post':
        method = _wp_show_post()
    elif args.type == 'page':
        method = _wp_show_page()
    elif args.type == 'draft':
        for adir, aname, afile in conf.get_mdfiles('draft'):
            slog.info(afile)
    elif args.type == 'option':
        method = GetOptions([])
    elif args.type == 'tax':
        method = GetTaxonomies()
    elif args.type == 'term':
        terms = _get_terms_from_wp(args.query)
        if terms:
            _print_results(terms)
        else:
            slog.warning('No term %s!'%str(args.query))

    if not method:
        return

    results = _wpcall(method)
    if not results:
        slog.warning('No results for showing.')
        return

    _print_results(results)

def _wp_show_page():
    field = {'post_type':'page'}
    field['number'] = args.number
    field['orderby'] = args.orderby
    field['order'] = args.order

    if args.query:
        return GetPost(_get_postid(), result_class=WordPressPage)
    return GetPosts(field, result_class=WordPressPage)

def _wp_show_post():
    field = {}
    field['number'] = args.number
    field['orderby'] = args.orderby
    field['order'] = args.order

    if args.query:
        return GetPost(_get_postid())
    return GetPosts(field)

def build(gconf, gargs, parser=None):
    global conf
    global args
    conf = gconf
    args = gargs

    noAnyArgs = True
    if args.user:
        conf.site.user = args.user
    if args.password:
        conf.site.password = args.password
    if args.site:
        if args.site.rfind('xmlrpc.php')>0:
            conf.site.url = args.site
        else:
            removeslash = args.site.rfind('/')
            if removeslash == len(args.site)-1:
                removeslash = args.site[0:removeslash]
            else:
                removeslash = args.site
            conf.site.url = '%s/xmlrpc.php'%removeslash
    if args.action:
        eval('_wp_'+args.action)()
        noAnyArgs = False

    if noAnyArgs and parser:
        parser.print_help()

